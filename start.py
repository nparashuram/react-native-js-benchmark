#!/usr/bin/env python
import argparse
import glob
import json
import os
import sys
import tempfile
from lib.colorful import colorful
from lib.logger import (get_logger, setup_logger)
from lib.section import (h1, h2)
from lib.tools import (AdbTool, ApkTool)

ROOT_DIR = os.path.dirname(os.path.realpath(__file__))

logger = get_logger(__name__)


class RenderComponentThroughput:
    def __init__(self, app_id, interval):
        self._app_id = app_id
        self._interval = interval

    def run(self):
        AdbTool.stop_apps()
        AdbTool.clear_log()
        AdbTool.start_with_link(
            self._app_id,
            '/RenderComponentThroughput?interval={}'.format(self._interval))
        result = AdbTool.wait_for_console_log(r'count=(\d+)').group(1)
        memory = AdbTool.get_memory(self._app_id)
        return (int(result), int(memory))

    def run_with_average(self, times):
        ret = {
            'result': 0,
            'memory': 0,
        }
        for _ in range(times):
            (result, memory) = self.run()
            ret['result'] += result
            ret['memory'] += memory

        # NOTE(kudo): Keeps thing simpler to trim as integer
        ret['result'] = int(ret['result'] / times)
        ret['memory'] = int(ret['memory'] / times)
        return ret


class TTI:
    def __init__(self, app_id, size):
        self._app_id = app_id
        self._size = size

    def run(self, apk_install_kwargs):
        data_file_path = os.path.join(ROOT_DIR, 'src', 'TTI', 'data.json')
        with self.PatchBundleContext(data_file_path, self._size):
            ApkTool.reinstall(**apk_install_kwargs)
            result = self._run_batch_with_average(3)
            logger.info('{app} {result}'.format(
                app=self._app_id, result=result))

    class PatchBundleContext:
        def __init__(self, data_file_path, size):
            self._data_file_path = data_file_path
            self._size = size

        def __enter__(self):
            os.rename(self._data_file_path, self._data_file_path + '.bak')
            with open(self._data_file_path, 'w') as f:
                f.write(self._generate_json_string(self._size))

        def _generate_json_string(self, size):
            data = {
                'description': 'GENERATE_FAKE_DATA',
                'size': size,
                'data': 'a' * size,
            }
            return json.dumps(data)

        def __exit__(self, type, value, traceback):
            os.rename(self._data_file_path + '.bak', self._data_file_path)

    @classmethod
    def _wait_for_tti_log(cls):
        return int(AdbTool.wait_for_log(r'TTI=(\d+)', 'MeasureTTI').group(1))

    @classmethod
    def _start(cls, app_id):
        os.system(
            'adb shell am start -a android.intent.action.VIEW -d "rnbench://{}/TTI" > /dev/null'
            .format(app_id))

    def _run_batch(self):
        AdbTool.stop_apps()
        AdbTool.clear_log()
        self._start(self._app_id)
        return self._wait_for_tti_log()

    def _run_batch_with_average(self, times):
        result = 0
        for _ in range(times):
            result += self._run_batch()
        result = int(result / times)
        return result


class JSDistManager:
    STORE_DIST_DIR = os.path.join(ROOT_DIR, 'js_dist')
    DISTS = {
        'jsc_official_245459': {
            'download_url':
            'https://registry.npmjs.org/jsc-android/-/jsc-android-245459.0.0.tgz',
            'version':
            '245459.0.0',
            'meta': ('Baseline JIT (but not x86)', 'WebKitGTK 2.24.2',
                     'Support Intl'),
            'aar_glob':
            '**/android-jsc-intl/**/*.aar',
            'binary_name':
            'libjsc.so',
        },
        'jsc_245459_no_jit': {
            'download_url':
            'https://registry.npmjs.org/@kudo-ci/jsc-android/-/jsc-android-245459.0.0-no-jit.tgz',
            'version':
            '245459.0.0-no-jit',
            'meta': ('JIT-less', 'WebKitGTK 2.24.2', 'Support Intl'),
            'aar_glob':
            '**/android-jsc-intl/**/*.aar',
            'binary_name':
            'libjsc.so',
        },
        'v8_751': {
            'download_url':
            'https://registry.npmjs.org/v8-android/-/v8-android-7.5.1.tgz',
            'version':
            '7.5.1',
            'meta': ('JIT-less (but not arm64-v8a)', 'V8 7.5.288.23',
                     'Support Intl'),
            'aar_glob':
            '**/*.aar',
            'binary_name':
            'libv8.so',
        },
        'v8_751_jit': {
            'download_url':
            'https://registry.npmjs.org/v8-android/-/v8-android-7.5.1-jit.tgz',
            'version':
            '7.5.1',
            'meta': ('JIT', 'V8 7.5.288.23', 'Support Intl'),
            'aar_glob':
            '**/*.aar',
            'binary_name':
            'libv8.so',
        },
    }

    def __init__(self, dist_id):
        self._dist_id = dist_id
        self._dist_info = self.DISTS[dist_id]

    def prepare(self):
        js_dist_path = os.path.join(self.STORE_DIST_DIR, self._dist_id)
        maven_dist_path = os.path.join(js_dist_path, 'package', 'dist')
        if not os.path.isdir(maven_dist_path):
            logger.info('JSDistManager::prepare() - Download and extract\n')
            os.system('mkdir -p {}'.format(js_dist_path))
            self._download_dist(self._dist_info['download_url'], js_dist_path)
        return maven_dist_path

    def get_binary_size(self, abi=None):
        js_dist_path = os.path.join(self.STORE_DIST_DIR, self._dist_id)
        if not os.path.exists(js_dist_path):
            raise RuntimeError('js_dist_path is not existed - ' + js_dist_path)
        aar_paths = glob.glob(
            os.path.join(js_dist_path, self._dist_info['aar_glob']),
            recursive=True)
        if len(aar_paths) < 1:
            return -1
        aar_path = aar_paths[0]
        _abi = abi or 'armeabi-v7a'
        binary_path = os.path.join('jni', _abi, self._dist_info['binary_name'])
        output_file = tempfile.NamedTemporaryFile(delete=False)
        output_path = output_file.name
        output_file.close()
        cmd = 'unzip -p {aar_path} {binary_path} > {output_path}'.format(
            aar_path=aar_path,
            binary_path=binary_path,
            output_path=output_path)
        logger.debug('get_binary_size - cmd: {}'.format(cmd))
        os.system(cmd)
        size = os.path.getsize(output_path)
        self._strip_binary(output_path, _abi)
        size = os.path.getsize(output_path)
        os.unlink(output_path)
        return size

    @property
    def info(self):
        return self._dist_info

    @classmethod
    def _download_dist(cls, url, output_path):
        cmd = 'wget -O- "{url}" | tar x - -C "{output_path}"'.format(
            url=url, output_path=output_path)
        logger.debug('download_dist - cmd: {}'.format(cmd))
        os.system(cmd)

    @classmethod
    def _strip_binary(cls, file_path, abi):
        ndk_path = os.environ['NDK_PATH']
        if not ndk_path:
            raise RuntimeError('NDK_PATH environment variable is not defined.')

        mappings = {
            'armeabi-v7a': 'arm-linux-androideabi-*',
            'arm64-v8a': 'aarch64-linux-android-*',
            'x86': 'x86-*',
            'x86_64': 'x86_64-*',
        }
        strip_tool_paths = glob.glob(
            os.path.join(ndk_path, 'toolchains', mappings[abi], '**',
                         '*-strip'),
            recursive=True)
        if len(strip_tool_paths) < 1:
            raise RuntimeError('Unable to find strip from NDK toolchains')
        strip_tool_path = strip_tool_paths[0]
        cmd = strip_tool_path + ' ' + file_path
        logger.debug('strip_binary - cmd: {}'.format(cmd))
        os.system(cmd)


def show_configs(abi, jsc_dist_manager, v8_dist_manager):
    logger.info('ABI: {}\n'.format(abi or 'default'))

    logger.info('JSC version: {}\nJSC meta: {}\nJSC binary size: {}\n'.format(
        jsc_dist_manager.info['version'],
        ', '.join(jsc_dist_manager.info['meta']),
        jsc_dist_manager.get_binary_size(abi)))
    logger.info('V8 version: {}\nV8 meta: {}\nV8 binary size: {}\n'.format(
        v8_dist_manager.info['version'],
        ', '.join(v8_dist_manager.info['meta']),
        v8_dist_manager.get_binary_size(abi)))


def parse_args():
    arg_parser = argparse.ArgumentParser()

    arg_parser.add_argument(
        '--verbose', '-v', action='store_true', help='Enable verbose log')
    arg_parser.add_argument(
        '--all', '-a', action='store_true', help='Run all benchmarks')
    arg_parser.add_argument(
        '--config-only', action='store_true', help='Show JS dist config only')
    arg_parser.add_argument(
        'suites',
        nargs='*',
        help=
        'Benchmark suites to run - supported arguments: RenderComponentThroughput, TTI'
    )

    args = arg_parser.parse_args()
    if not any((args.all, args.config_only)) and len(args.suites) == 0:
        arg_parser.print_help()
        sys.exit(1)
    return args


class RenderComponentThroughputSuite:
    def run(self, jsc_apk_install_kwargs, v8_apk_install_kwargs, hermes_apk_install_kwargs):
        logger.info(h1('RenderComponentThroughput Suite'))
        ApkTool.reinstall(**jsc_apk_install_kwargs)
        ApkTool.reinstall(**v8_apk_install_kwargs)
        ApkTool.reinstall(**hermes_apk_install_kwargs)

        logger.info(h2('RenderComponentThroughput 10s'))
        logger.info('jsc {}'.format(
            RenderComponentThroughput('jsc', 10000).run_with_average(3)))
        logger.info('v8 {}'.format(
            RenderComponentThroughput('v8', 10000).run_with_average(3)))
        logger.info('hermes {}'.format(
            RenderComponentThroughput('v8', 10000).run_with_average(3)))

        logger.info(h2('RenderComponentThroughput 60s'))
        logger.info('jsc {}'.format(
            RenderComponentThroughput('jsc', 60000).run_with_average(3)))
        logger.info('v8 {}'.format(
            RenderComponentThroughput('v8', 60000).run_with_average(3)))
        logger.info('hermes {}'.format(
            RenderComponentThroughput('v8', 60000).run_with_average(3)))

        logger.info(h2('RenderComponentThroughput 180s'))
        logger.info('jsc {}'.format(
            RenderComponentThroughput('jsc', 180000).run_with_average(3)))
        logger.info('v8 {}'.format(
            RenderComponentThroughput('v8', 180000).run_with_average(3)))
        logger.info('hermes {}'.format(
            RenderComponentThroughput('v8', 180000).run_with_average(3)))


class TTISuite:
    def run(self, jsc_apk_install_kwargs, v8_apk_install_kwargs, hermes_apk_install_kwargs):
        logger.info(h1('TTI Suite'))

        logger.info(h2('TTI 3MiB'))
        size = 1024 * 1024 * 3
        TTI('jsc', size).run(jsc_apk_install_kwargs)
        TTI('v8', size).run(v8_apk_install_kwargs)
        TTI('hermes', size).run(v8_apk_install_kwargs)

        logger.info(h2('TTI 10MiB'))
        size = 1024 * 1024 * 10
        TTI('jsc', size).run(jsc_apk_install_kwargs)
        TTI('v8', size).run(v8_apk_install_kwargs)
        TTI('hermes', size).run(v8_apk_install_kwargs)

        logger.info(h2('TTI 15MiB'))
        size = 1024 * 1024 * 15
        TTI('jsc', size).run(jsc_apk_install_kwargs)
        TTI('v8', size).run(v8_apk_install_kwargs)
        TTI('hermes', size).run(v8_apk_install_kwargs)


def main():
    args = parse_args()
    setup_logger(logger, args.verbose)

    suites = []
    if args.all or 'RenderComponentThroughput' in args.suites:
        suites.append(RenderComponentThroughputSuite())
    if args.all or 'TTI' in args.suites:
        suites.append(TTISuite())

    # {armeabi-v7a, arm64-v8a, x86, x86_64}
    abi = 'armeabi-v7a'
    # abi = 'x86'
    jsc_dist_manager = JSDistManager('jsc_official_245459')
    jsc_dist_manager.prepare()

    v8_dist_manager = JSDistManager('v8_751')
    v8_dist_manager.prepare()

    logger.info(h1('Config'))
    show_configs(abi, jsc_dist_manager, v8_dist_manager)

    jsc_apk_install_kwargs = {
        'app_id': 'jsc',
        'maven_repo_prop': 'JSC_DIST_REPO=' + jsc_dist_manager.prepare(),
        'abi': abi,
        'verbose': args.verbose,
    }

    v8_apk_install_kwargs = {
        'app_id': 'v8',
        'maven_repo_prop': 'V8_DIST_REPO=' + v8_dist_manager.prepare(),
        'abi': abi,
        'verbose': args.verbose,
    }

    hermes_apk_install_kwargs = {
        'app_id': 'hermes',
        'maven_repo_prop': 'V8_DIST_REPO=' + v8_dist_manager.prepare(),
        'abi': abi,
        'verbose': args.verbose,
    }

    for suite in suites:
        suite.run(jsc_apk_install_kwargs, v8_apk_install_kwargs, hermes_apk_install_kwargs)

    return 0


if __name__ == '__main__':
    os.chdir(ROOT_DIR)
    main()
