/**
 * @format
 * @flow
 */

import React, { useEffect, useState } from 'react';
import { View, Text, Linking, StyleSheet } from 'react-native';
import URL from 'url-parse';

const ROUTES = {
  '/RenderComponentThroughput': require('./src/ReactRender/RenderComponentThroughput').default,
  '/TTI': require('./src/TTI/TTIView').default,
};

const NotFoundView = () => (
  <View style={styles.notFoundView}>
    <Text style={styles.notFoundText}>View not found</Text>
  </View>
);

const renderEngine = () => {
  if (typeof global.HermesInternal === 'object') {
    return 'Hermes'
  } else if (typeof global._v8runtime === 'function') {
    return 'V8:' + global._v8runtime().version;
  } else {
    return 'JSC'
  }
}

const App = () => {
  const [route, setRoute] = useState(null);
  useEffect(() => {
    async function setupInitialRoute() {
      const initialUrl = await Linking.getInitialURL();
      if (initialUrl && initialUrl.startsWith('rnbench://')) {
        const url = new URL(initialUrl, '', true);
        setRoute(url);
      }
    }

    setupInitialRoute();
  }, []);

  return (
    <View style={styles.container}>
      {route != null && ROUTES[route.pathname] != null ? (
        React.createElement(ROUTES[route.pathname], route.query)
      ) : (
        <NotFoundView />
      )}
      {__DEV__ && <Text>{renderEngine()}</Text>}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
  },
  notFoundView: {
    flex: 1,
    backgroundColor: 'rgb(192, 186, 48)',
    flexDirection: 'column',
    justifyContent: 'center',
  },
  notFoundText: {
    color: 'rgb(255, 255, 255)',
    fontSize: 32,
    textAlign: 'center',
  },
});

export default App;
