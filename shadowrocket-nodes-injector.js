/*!
 * frontier-chain-skeleton — Shadowrocket 节点订阅脚本
 *
 * 通过 Sub-Store ShadowRocket producer + 此 Script Operator 注入家宽链路节点
 * 入口签名: function operator(proxies, targetPlatform, context)  (Sub-Store 节点处理)
 *
 * 凭据通过 Sub-Store 订阅 URL fragment 注入 ($arguments):
 *   ?target=ShadowRocket#vps_server=<host>&vps_password=<pass>&vps_port=<port>&vps_cipher=<cipher>
 *
 * 节点 name 固定为 "🏠 [VPS→家宽] Frontier"，与 shadowrocket.conf 的
 *   policy-regex-filter=Frontier|🏠
 * 配合，让 [Proxy Group] 自动捕获，避免任何手维护节点列表。
 *
 * 安全约束:
 *   - 不写默认凭据、不硬编码 server/password
 *   - 凭据缺失时静默跳过 (不抛异常, 不阻塞订阅)
 *   - 不引入 ScrapeGW 链路 (iPhone 端只保留 [VPS→家宽] 一条)
 */

function getCred(key) {
  try {
    if (typeof $arguments !== 'undefined' && $arguments && key in $arguments) {
      return $arguments[key];
    }
  } catch (e) {
    /* $arguments 在某些环境下是 ReferenceError, 静默 */
  }
  return undefined;
}

function buildVpsFrontierNode() {
  const server = getCred('vps_server');
  const password = getCred('vps_password');
  if (!server || !password) {
    if (typeof console !== 'undefined' && console.log) {
      console.log('[shadowrocket-injector] vps_server / vps_password 未提供，跳过 [VPS→家宽] 节点');
    }
    return null;
  }
  const portRaw = getCred('vps_port');
  const port = parseInt(portRaw || '51388', 10);
  const cipher = getCred('vps_cipher') || 'chacha20-ietf-poly1305';
  return {
    name: '🏠 [VPS→家宽] Frontier',
    type: 'ss',
    server: server,
    port: port,
    cipher: cipher,
    password: password,
    udp: true,
    'no-resolve': false,
  };
}

function operator(proxies, targetPlatform, context) {
  const node = buildVpsFrontierNode();
  if (node) {
    const exists = Array.isArray(proxies) && proxies.some(function (p) {
      return p && p.name === node.name;
    });
    if (!exists) proxies.push(node);
  }
  return proxies;
}

if (typeof globalThis !== 'undefined') {
  globalThis.operator = operator;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { operator: operator, buildVpsFrontierNode: buildVpsFrontierNode };
}
