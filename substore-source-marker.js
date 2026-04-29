/*!
 * Sub-Store source marker
 *
 * Put this Script Operator on each upstream subscription before merging into
 * the collection. It tags proxy objects with an internal source prefix, then
 * shadowrocket-nodes-injector.js consumes and removes the marker.
 */

function getArg(key) {
  try {
    if (typeof $arguments !== 'undefined' && $arguments && $arguments[key] != null) {
      return $arguments[key];
    }
  } catch (e) {
    /* ignore ReferenceError in non-Sub-Store runtimes */
  }
  return undefined;
}

function operator(proxies) {
  if (!Array.isArray(proxies)) return proxies;
  var prefix = String(getArg('source_prefix') || getArg('prefix') || '').trim().toUpperCase();
  if (!prefix) return proxies;
  for (var i = 0; i < proxies.length; i++) {
    if (proxies[i] && typeof proxies[i] === 'object') {
      proxies[i].__sourcePrefix = prefix;
    }
  }
  return proxies;
}

if (typeof globalThis !== 'undefined') {
  globalThis.operator = operator;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { operator: operator };
}
