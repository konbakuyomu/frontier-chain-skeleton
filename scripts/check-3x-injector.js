const assert = require('assert');
const injector = require('../shadowrocket-nodes-injector.js');

const proxies = [
  { name: 'SJC-3X raw reality', type: 'vless', __sourcePrefix: 'SJC-3X' },
  { name: 'MALAYSIA-3X anything HY2', type: 'hysteria2', __sourcePrefix: 'MALAYSIA-3X' },
  { name: 'OLD-US-3X | legacy vless label', type: 'vless' },
  { name: 'OLD-US-3X | legacy HY2 label', type: 'hysteria2' },
  { name: 'EDGE-US | 美国-VPS直出', type: 'vmess' },
  { name: 'EDGE-US | 美国-VPS直出-HY2', type: 'hysteria2' },
  { name: 'EDGE-US | 美国-VPS直出-HY2-带宽', type: 'hysteria2' },
];

const normalized = injector.normalizeAirportProxies(proxies);
const names = normalized.map((proxy) => proxy.name);

assert.deepStrictEqual(names, [
  'SJC-3X | 美国-SJC-VLESS',
  'MALAYSIA-3X | 马来西亚-HY2',
  'OLD-US-3X | 美国旧机-VLESS',
  'OLD-US-3X | 美国旧机-HY2',
  'EDGE-US | 美国-VPS直出',
  'EDGE-US | 美国-VPS直出-HY2',
  'EDGE-US | 美国-VPS直出-HY2-带宽',
]);

assert.strictEqual(injector.isHy2OnlyNode(normalized[0]), false);
assert.strictEqual(injector.isHy2OnlyNode(normalized[1]), true);
assert.strictEqual(injector.isHy2OnlyNode(normalized[2]), false);
assert.strictEqual(injector.isHy2OnlyNode(normalized[3]), true);
assert.strictEqual(injector.isHy2OnlyNode(normalized[4]), false);
assert.strictEqual(injector.isHy2OnlyNode(normalized[5]), true);
assert.strictEqual(injector.isHy2OnlyNode(normalized[6]), true);

console.log('3x injector checks passed');
