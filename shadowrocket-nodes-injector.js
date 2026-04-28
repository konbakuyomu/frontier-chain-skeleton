/*!
 * frontier-chain-skeleton — Shadowrocket 节点订阅脚本
 *
 * 通过 Sub-Store ShadowRocket producer + 此 Script Operator 归一化节点名并注入家宽链路节点
 * 入口签名: function operator(proxies, targetPlatform, context)  (Sub-Store 节点处理)
 *
 * 凭据通过 Sub-Store Script Operator arguments 注入 ($arguments):
 *   {"vps_server":"<host>","vps_password":"<pass>","vps_port":"<port>","vps_cipher":"<cipher>"}
 *
 * 自建节点 name 固定为 "🏠 [VPS→家宽] Frontier"，与 shadowrocket.conf 的
 *   policy-regex-filter=\[VPS→家宽\]
 * 配合，让 [Proxy Group] 自动捕获，避免任何手维护节点列表。
 *
 * 安全约束:
 *   - 不写默认凭据、不硬编码 server/password
 *   - 凭据缺失时静默跳过 (不抛异常, 不阻塞订阅)
 *   - 不引入 ScrapeGW 链路 (iPhone 端只保留 [VPS→家宽] 一条)
 */

const FRONTIER_NODE_NAME = '🏠 [VPS→家宽] Frontier';
const INFO_PSEUDO_NODE_NAME_PATTERN = /(剩余流量|距离下次重置|套餐到期)/;
const FLAG_PREFIX_PATTERN = /^(?:\uD83C[\uDDE6-\uDDFF]){2}\s*/;

const REGION_DEFINITIONS = [
  {
    code: 'HK',
    flag: '🇭🇰',
    detect: [
      /香港|Hong\s*Kong/i,
      /(?:^|[\s/_-])HK(?:\d+|\b)/i,
    ],
    remove: [/香港|Hong\s*Kong/i, /(?:^|[\s/_-])HK(?=$|[\s/_-])/i],
  },
  {
    code: 'TW',
    flag: '🇹🇼',
    detect: [
      /台湾|台灣|台北|台中|新北|彰化|Taiwan|Taipei/i,
      /(?:^|[\s/_-])TW(?:\d+|\b)/i,
    ],
    remove: [/台湾|台灣|台北|台中|新北|彰化|Taiwan|Taipei/i, /(?:^|[\s/_-])TW(?=$|[\s/_-])/i],
  },
  {
    code: 'JP',
    flag: '🇯🇵',
    detect: [
      /日本|东京|東京|大阪|Japan|Tokyo|Osaka/i,
      /(?:^|[\s/_-])JP(?:\d+|\b)/i,
    ],
    remove: [/日本|东京|東京|大阪|Japan|Tokyo|Osaka/i, /(?:^|[\s/_-])JP(?=$|[\s/_-])/i],
  },
  {
    code: 'US',
    flag: '🇺🇸',
    detect: [
      /美国|美國|沪美|廣美|广美|京美|美西|美东|美東|America|United\s*States|Los\s*Angeles|Seattle|Chicago|New\s*York|Phoenix/i,
      /(?:^|[\s/_-])US(?:A|\d+|\b)/i,
    ],
    remove: [
      /美国|美國|沪美|廣美|广美|京美|美西|美东|美東|America|United\s*States|Los\s*Angeles|Seattle|Chicago|New\s*York|Phoenix/i,
      /(?:^|[\s/_-])USA(?=$|[\s/_-])/i,
      /(?:^|[\s/_-])US(?=$|[\s/_-])/i,
    ],
  },
  {
    code: 'SG',
    flag: '🇸🇬',
    detect: [/新加坡|狮城|獅城|Singapore/i, /(?:^|[\s/_-])SG(?:\d+|\b)/i],
    remove: [/新加坡|狮城|獅城|Singapore/i, /(?:^|[\s/_-])SG(?=$|[\s/_-])/i],
  },
  {
    code: 'KR',
    flag: '🇰🇷',
    detect: [/韩国|韓國|首尔|首爾|Korea|Seoul/i, /(?:^|[\s/_-])KR(?:\d+|\b)/i],
    remove: [/韩国|韓國|首尔|首爾|Korea|Seoul/i, /(?:^|[\s/_-])KR(?=$|[\s/_-])/i],
  },
  {
    code: 'DE',
    flag: '🇩🇪',
    detect: [/德国|德國|法兰克福|法蘭克福|Germany|Deutschland|Frankfurt/i, /(?:^|[\s/_-])DE(?:\d+|\b)/i],
    remove: [/德国|德國|法兰克福|法蘭克福|Germany|Deutschland|Frankfurt/i, /(?:^|[\s/_-])DE(?=$|[\s/_-])/i],
  },
  {
    code: 'GB',
    flag: '🇬🇧',
    detect: [/英国|英國|伦敦|倫敦|United\s*Kingdom|Britain|London/i, /(?:^|[\s/_-])(?:UK|GB)(?:\d+|\b)/i],
    remove: [/英国|英國|伦敦|倫敦|United\s*Kingdom|Britain|London/i, /(?:^|[\s/_-])(?:UK|GB)(?=$|[\s/_-])/i],
  },
  {
    code: 'FR',
    flag: '🇫🇷',
    detect: [/法国|法國|巴黎|France|Paris/i, /(?:^|[\s/_-])FR(?:\d+|\b)/i],
    remove: [/法国|法國|巴黎|France|Paris/i, /(?:^|[\s/_-])FR(?=$|[\s/_-])/i],
  },
  {
    code: 'NL',
    flag: '🇳🇱',
    detect: [/荷兰|荷蘭|阿姆斯特丹|Netherlands|Amsterdam/i, /(?:^|[\s/_-])NL(?:\d+|\b)/i],
    remove: [/荷兰|荷蘭|阿姆斯特丹|Netherlands|Amsterdam/i, /(?:^|[\s/_-])NL(?=$|[\s/_-])/i],
  },
  {
    code: 'CA',
    flag: '🇨🇦',
    detect: [/加拿大|Canada|Toronto|Vancouver/i, /(?:^|[\s/_-])CA(?:\d+|\b)/i],
    remove: [/加拿大|Canada|Toronto|Vancouver/i, /(?:^|[\s/_-])CA(?=$|[\s/_-])/i],
  },
  {
    code: 'AU',
    flag: '🇦🇺',
    detect: [/澳大利亚|澳洲|悉尼|墨尔本|Australia|Sydney|Melbourne/i, /(?:^|[\s/_-])AU(?:\d+|\b)/i],
    remove: [/澳大利亚|澳洲|悉尼|墨尔本|Australia|Sydney|Melbourne/i, /(?:^|[\s/_-])AU(?=$|[\s/_-])/i],
  },
];

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

function stripLeadingFlag(name) {
  return String(name || '').replace(FLAG_PREFIX_PATTERN, '').trim();
}

function extractRouteTag(name) {
  const withoutFlag = stripLeadingFlag(name);
  const bracketMatch = withoutFlag.match(/\[([^\]]+)\]/);
  if (bracketMatch) return '[' + bracketMatch[1].trim() + ']';

  const prefixMatch = withoutFlag.match(/^(广东|广州|深圳|上海|北京|江苏|浙江|福建|沪|京|广|深)\s*[-–—]/);
  if (prefixMatch) return '[' + prefixMatch[1].trim() + ']';

  return '';
}

function removeRouteTag(name) {
  return stripLeadingFlag(name)
    .replace(/\[[^\]]+\]\s*/g, '')
    .replace(/^(广东|广州|深圳|上海|北京|江苏|浙江|福建|沪|京|广|深)\s*[-–—]\s*/, '')
    .trim();
}

function detectExitRegion(name) {
  const cleanedName = removeRouteTag(name);
  for (const region of REGION_DEFINITIONS) {
    if (region.detect.some(function (pattern) { return pattern.test(cleanedName); })) {
      return region;
    }
  }
  return null;
}

function cleanupProviderLabel(label) {
  return String(label || '')
    .replace(FLAG_PREFIX_PATTERN, '')
    .replace(/\s+[-–—]\s+|\s+[-–—]|[-–—]\s+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractProviderLabel(name, region) {
  let provider = removeRouteTag(name);
  for (const pattern of region.remove) {
    provider = provider.replace(pattern, ' ');
  }
  return cleanupProviderLabel(provider);
}

function normalizeAirportNodeName(name) {
  if (!name || name === FRONTIER_NODE_NAME || INFO_PSEUDO_NODE_NAME_PATTERN.test(name)) {
    return name;
  }

  const region = detectExitRegion(name);
  if (!region) return name;

  const provider = extractProviderLabel(name, region);
  if (!provider) return name;

  const routeTag = extractRouteTag(name);
  return [region.flag, region.code, provider, routeTag].filter(Boolean).join(' ');
}

function isInfoPseudoNode(proxy) {
  return Boolean(proxy && typeof proxy.name === 'string' && INFO_PSEUDO_NODE_NAME_PATTERN.test(proxy.name));
}

function normalizeAirportProxies(proxies) {
  if (!Array.isArray(proxies)) return proxies;

  let frontierSeen = false;
  return proxies.reduce(function (normalized, proxy) {
    if (!proxy || typeof proxy.name !== 'string') {
      normalized.push(proxy);
      return normalized;
    }

    if (proxy.name === FRONTIER_NODE_NAME) {
      if (!frontierSeen) {
        frontierSeen = true;
        normalized.push(proxy);
      }
      return normalized;
    }

    if (isInfoPseudoNode(proxy)) return normalized;

    const normalizedName = normalizeAirportNodeName(proxy.name);
    if (normalizedName !== proxy.name) {
      normalized.push(Object.assign({}, proxy, { name: normalizedName }));
    } else {
      normalized.push(proxy);
    }
    return normalized;
  }, []);
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
    name: FRONTIER_NODE_NAME,
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
  const normalizedProxies = normalizeAirportProxies(proxies);
  const node = buildVpsFrontierNode();
  if (node) {
    const exists = Array.isArray(normalizedProxies) && normalizedProxies.some(function (p) {
      return p && p.name === node.name;
    });
    if (!exists) normalizedProxies.push(node);
  }
  return normalizedProxies;
}

if (typeof globalThis !== 'undefined') {
  globalThis.operator = operator;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    operator: operator,
    buildVpsFrontierNode: buildVpsFrontierNode,
    normalizeAirportNodeName: normalizeAirportNodeName,
    normalizeAirportProxies: normalizeAirportProxies,
    isInfoPseudoNode: isInfoPseudoNode,
  };
}
