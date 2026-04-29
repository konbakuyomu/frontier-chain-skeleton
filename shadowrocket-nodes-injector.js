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
const DEFAULT_SOURCE_PREFIX_MAP = {
  ccrui: 'CCR',
  ccr: 'CCR',
  kuma: 'KUMA',
};
const SOURCE_PREFIX_FIELDS = [
  '__sourcePrefix',
  '_sourcePrefix',
  'sourcePrefix',
  '__substore_source',
  '_substore_source',
  'subName',
  'subscription',
  'subscriptionName',
  'source',
  'sourceName',
  'provider',
];

const REGION_DEFINITIONS = [
  {
    code: 'HK',
    display: '香港',
    flag: '🇭🇰',
    detect: [
      /香港|Hong\s*Kong/i,
      /(?:^|[\s/_-])HK(?:\d+|\b)/i,
    ],
    remove: [/香港|Hong\s*Kong/i, /(?:^|[\s/_-])HK(?=$|[\s/_-])/i],
  },
  {
    code: 'TW',
    display: '台湾',
    flag: '🇹🇼',
    detect: [
      /台湾|台灣|台北|台中|新北|彰化|Taiwan|Taipei/i,
      /(?:^|[\s/_-])TW(?:\d+|\b)/i,
    ],
    remove: [/台湾|台灣|台北|台中|新北|彰化|Taiwan|Taipei/i, /(?:^|[\s/_-])TW(?=$|[\s/_-])/i],
  },
  {
    code: 'JP',
    display: '日本',
    flag: '🇯🇵',
    detect: [
      /日本|东京|東京|大阪|Japan|Tokyo|Osaka/i,
      /(?:^|[\s/_-])JP(?:\d+|\b)/i,
    ],
    remove: [/日本|东京|東京|大阪|Japan|Tokyo|Osaka/i, /(?:^|[\s/_-])JP(?=$|[\s/_-])/i],
  },
  {
    code: 'US',
    display: '美国',
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
    display: '新加坡',
    flag: '🇸🇬',
    detect: [/新加坡|狮城|獅城|Singapore/i, /(?:^|[\s/_-])SG(?:\d+|\b)/i],
    remove: [/新加坡|狮城|獅城|Singapore/i, /(?:^|[\s/_-])SG(?=$|[\s/_-])/i],
  },
  {
    code: 'KR',
    display: '韩国',
    flag: '🇰🇷',
    detect: [/韩国|韓國|首尔|首爾|Korea|Seoul/i, /(?:^|[\s/_-])KR(?:\d+|\b)/i],
    remove: [/韩国|韓國|首尔|首爾|Korea|Seoul/i, /(?:^|[\s/_-])KR(?=$|[\s/_-])/i],
  },
  {
    code: 'DE',
    display: '德国',
    flag: '🇩🇪',
    detect: [/德国|德國|法兰克福|法蘭克福|Germany|Deutschland|Frankfurt/i, /(?:^|[\s/_-])DE(?:\d+|\b)/i],
    remove: [/德国|德國|法兰克福|法蘭克福|Germany|Deutschland|Frankfurt/i, /(?:^|[\s/_-])DE(?=$|[\s/_-])/i],
  },
  {
    code: 'GB',
    display: '英国',
    flag: '🇬🇧',
    detect: [/英国|英國|伦敦|倫敦|United\s*Kingdom|Britain|London/i, /(?:^|[\s/_-])(?:UK|GB)(?:\d+|\b)/i],
    remove: [/英国|英國|伦敦|倫敦|United\s*Kingdom|Britain|London/i, /(?:^|[\s/_-])(?:UK|GB)(?=$|[\s/_-])/i],
  },
  {
    code: 'FR',
    display: '法国',
    flag: '🇫🇷',
    detect: [/法国|法國|巴黎|France|Paris/i, /(?:^|[\s/_-])FR(?:\d+|\b)/i],
    remove: [/法国|法國|巴黎|France|Paris/i, /(?:^|[\s/_-])FR(?=$|[\s/_-])/i],
  },
  {
    code: 'NL',
    display: '荷兰',
    flag: '🇳🇱',
    detect: [/荷兰|荷蘭|阿姆斯特丹|Netherlands|Amsterdam/i, /(?:^|[\s/_-])NL(?:\d+|\b)/i],
    remove: [/荷兰|荷蘭|阿姆斯特丹|Netherlands|Amsterdam/i, /(?:^|[\s/_-])NL(?=$|[\s/_-])/i],
  },
  {
    code: 'CA',
    display: '加拿大',
    flag: '🇨🇦',
    detect: [/加拿大|Canada|Toronto|Vancouver/i, /(?:^|[\s/_-])CA(?:\d+|\b)/i],
    remove: [/加拿大|Canada|Toronto|Vancouver/i, /(?:^|[\s/_-])CA(?=$|[\s/_-])/i],
  },
  {
    code: 'AU',
    display: '澳大利亚',
    flag: '🇦🇺',
    detect: [/澳大利亚|澳洲|悉尼|墨尔本|Australia|Sydney|Melbourne/i, /(?:^|[\s/_-])AU(?:\d+|\b)/i],
    remove: [/澳大利亚|澳洲|悉尼|墨尔本|Australia|Sydney|Melbourne/i, /(?:^|[\s/_-])AU(?=$|[\s/_-])/i],
  },
  {
    code: 'RU',
    display: '俄罗斯',
    flag: '🇷🇺',
    detect: [/俄罗斯|俄羅斯|Russia|Moscow/i, /(?:^|[\s/_-])RU(?:\d+|\b)/i],
    remove: [/俄罗斯|俄羅斯|Russia|Moscow/i, /(?:^|[\s/_-])RU(?=$|[\s/_-])/i],
  },
  {
    code: 'TR',
    display: '土耳其',
    flag: '🇹🇷',
    detect: [/土耳其|Turkey|Istanbul/i, /(?:^|[\s/_-])TR(?:\d+|\b)/i],
    remove: [/土耳其|Turkey|Istanbul/i, /(?:^|[\s/_-])TR(?=$|[\s/_-])/i],
  },
  {
    code: 'BR',
    display: '巴西',
    flag: '🇧🇷',
    detect: [/巴西|Brazil|Sao\s*Paulo|São\s*Paulo/i, /(?:^|[\s/_-])BR(?:\d+|\b)/i],
    remove: [/巴西|Brazil|Sao\s*Paulo|São\s*Paulo/i, /(?:^|[\s/_-])BR(?=$|[\s/_-])/i],
  },
  {
    code: 'SE',
    display: '瑞典',
    flag: '🇸🇪',
    detect: [/瑞典|Sweden|Stockholm/i, /(?:^|[\s/_-])SE(?:\d+|\b)/i],
    remove: [/瑞典|Sweden|Stockholm/i, /(?:^|[\s/_-])SE(?=$|[\s/_-])/i],
  },
  {
    code: 'CH',
    display: '瑞士',
    flag: '🇨🇭',
    detect: [/瑞士|Switzerland|Zurich|Zürich/i, /(?:^|[\s/_-])CH(?:\d+|\b)/i],
    remove: [/瑞士|Switzerland|Zurich|Zürich/i, /(?:^|[\s/_-])CH(?=$|[\s/_-])/i],
  },
  {
    code: 'IN',
    display: '印度',
    flag: '🇮🇳',
    detect: [/印度|India|Mumbai|Delhi/i, /(?:^|[\s/_-])IN(?:\d+|\b)/i],
    remove: [/印度|India|Mumbai|Delhi/i, /(?:^|[\s/_-])IN(?=$|[\s/_-])/i],
  },
  {
    code: 'AR',
    display: '阿根廷',
    flag: '🇦🇷',
    detect: [/阿根廷|Argentina|Buenos\s*Aires/i, /(?:^|[\s/_-])AR(?:\d+|\b)/i],
    remove: [/阿根廷|Argentina|Buenos\s*Aires/i, /(?:^|[\s/_-])AR(?=$|[\s/_-])/i],
  },
  {
    code: 'UA',
    display: '乌克兰',
    flag: '🇺🇦',
    detect: [/乌克兰|烏克蘭|Ukraine|Kyiv|Kiev/i, /(?:^|[\s/_-])UA(?:\d+|\b)/i],
    remove: [/乌克兰|烏克蘭|Ukraine|Kyiv|Kiev/i, /(?:^|[\s/_-])UA(?=$|[\s/_-])/i],
  },
  {
    code: 'MY',
    display: '马来西亚',
    flag: '🇲🇾',
    detect: [/马来西亚|馬來西亞|Malaysia|Kuala\s*Lumpur/i, /(?:^|[\s/_-])MY(?:\d+|\b)/i],
    remove: [/马来西亚|馬來西亞|Malaysia|Kuala\s*Lumpur/i, /(?:^|[\s/_-])MY(?=$|[\s/_-])/i],
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
    .replace(/[｜|]+/g, ' ')
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

function getSourcePrefixMap() {
  var out = {};
  for (var key in DEFAULT_SOURCE_PREFIX_MAP) {
    if (Object.prototype.hasOwnProperty.call(DEFAULT_SOURCE_PREFIX_MAP, key)) {
      out[key] = DEFAULT_SOURCE_PREFIX_MAP[key];
    }
  }
  var extra = getCred('source_prefix_map');
  if (!extra) return out;
  try {
    if (typeof extra === 'string') extra = JSON.parse(extra);
    if (extra && typeof extra === 'object') {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) {
          out[String(k).toLowerCase()] = String(extra[k]).trim();
        }
      }
    }
  } catch (e) {
    if (typeof console !== 'undefined' && console.log) {
      console.log('[shadowrocket-injector] source_prefix_map 解析失败，使用默认映射');
    }
  }
  return out;
}

function normalizeSourcePrefixValue(value, map) {
  if (value == null) return '';
  var text = String(value).trim();
  if (!text) return '';
  if (/^(CCR|KUMA)$/i.test(text)) return text.toUpperCase();
  var lower = text.toLowerCase();
  for (var key in map) {
    if (Object.prototype.hasOwnProperty.call(map, key) && lower.indexOf(key) !== -1) {
      return map[key];
    }
  }
  return '';
}

function detectSourcePrefix(proxy) {
  var map = getSourcePrefixMap();
  if (proxy && typeof proxy === 'object') {
    for (var i = 0; i < SOURCE_PREFIX_FIELDS.length; i++) {
      var field = SOURCE_PREFIX_FIELDS[i];
      var detected = normalizeSourcePrefixValue(proxy[field], map);
      if (detected) return detected;
    }
  }
  return '';
}

function removeSourceMarkers(proxy) {
  if (!proxy || typeof proxy !== 'object') return;
  delete proxy.__sourcePrefix;
  delete proxy._sourcePrefix;
  delete proxy.sourcePrefix;
  delete proxy.__substore_source;
  delete proxy._substore_source;
}

function extractRateLabel(name) {
  var text = stripLeadingFlag(name);
  var hit = text.match(/(?:^|[\s/])(\d+(?:\.\d+)?x(?:\s*🌟|⭐|☀️|☀)?)/i);
  return hit ? hit[1].replace(/\s+/g, '') : '';
}

function removeRateLabel(label) {
  return String(label || '')
    .replace(/(?:^|[\s/])\d+(?:\.\d+)?x(?:\s*🌟|⭐|☀️|☀)?/ig, ' ')
    .replace(/\s*\/\s*/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function splitProviderParts(provider) {
  var cleaned = removeRateLabel(provider)
    .replace(/\b(?:Trojan|Vless|Vmess|Shadowsocks|SS)\b/ig, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  var parts = [];
  if (!cleaned) return parts;

  var knownAttrs = ['家宽', '商宽', '软银', '链式', '深港', 'Hinet', 'Seednet', 'HKT', 'DOIN', 'DonWeb'];
  for (var i = 0; i < knownAttrs.length; i++) {
    var attr = knownAttrs[i];
    var re = new RegExp(attr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i');
    if (re.test(cleaned)) {
      cleaned = cleaned.replace(re, ' ');
      parts.push(attr);
    }
  }

  cleaned = cleanupProviderLabel(cleaned);
  if (cleaned) parts.unshift(cleaned);
  return parts;
}

function routeTagToPart(routeTag) {
  return String(routeTag || '').replace(/^\[/, '').replace(/\]$/, '').trim();
}

function buildUnifiedName(sourcePrefix, regionName, provider, routeTag, rateLabel, fallbackName) {
  var parts = splitProviderParts(provider);
  var routePart = routeTagToPart(routeTag);
  if (routePart && parts.indexOf(routePart) === -1) parts.push(routePart);
  if (rateLabel && parts.indexOf(rateLabel) === -1) parts.push(rateLabel);
  if (parts.length === 0) {
    var fallback = cleanupProviderLabel(removeRateLabel(removeRouteTag(fallbackName)));
    if (fallback) parts.push(fallback);
  }
  var body = [regionName].concat(parts).filter(Boolean).join('-');
  return sourcePrefix ? sourcePrefix + ' | ' + body : body;
}

function normalizeAirportNodeName(name, proxy) {
  if (!name || name === FRONTIER_NODE_NAME || INFO_PSEUDO_NODE_NAME_PATTERN.test(name)) {
    return name;
  }

  const region = detectExitRegion(name);
  const sourcePrefix = detectSourcePrefix(proxy);
  if (!region && !sourcePrefix) return name;

  const provider = region ? extractProviderLabel(name, region) : cleanupProviderLabel(removeRouteTag(name));
  const routeTag = extractRouteTag(name);
  const rateLabel = extractRateLabel(name);
  return buildUnifiedName(
    sourcePrefix,
    region ? region.display : '其他',
    provider,
    routeTag,
    rateLabel,
    name
  );
}

function isInfoPseudoNode(proxy) {
  return Boolean(proxy && typeof proxy.name === 'string' && INFO_PSEUDO_NODE_NAME_PATTERN.test(proxy.name));
}

function normalizeAirportProxies(proxies) {
  if (!Array.isArray(proxies)) return proxies;

  // 关键策略：在 Sub-Store v2.22.8 沙箱里 operator 必须**原地 mutate proxy 对象**——
  // Sub-Store 内部保留了原始对象引用，最终序列化（target=ClashMeta）从那些原始对象读字段。
  // 用 Object.assign 返回新对象的写法在测试中观察到 dialer-proxy 修改不被序列化采纳。
  var frontierSeen = false;
  var renameMap = {};
  var intermediate = [];
  for (var i = 0; i < proxies.length; i++) {
    var proxy = proxies[i];
    if (!proxy || typeof proxy.name !== 'string') {
      intermediate.push(proxy);
      continue;
    }
    if (proxy.name === FRONTIER_NODE_NAME) {
      if (!frontierSeen) {
        frontierSeen = true;
        intermediate.push(proxy);
      }
      continue;
    }
    if (isInfoPseudoNode(proxy)) continue;
    var normalizedName = normalizeAirportNodeName(proxy.name, proxy);
    if (normalizedName !== proxy.name) {
      renameMap[proxy.name] = normalizedName;
      proxy.name = normalizedName;  // 原地 mutate
    }
    removeSourceMarkers(proxy);
    intermediate.push(proxy);
  }

  // Pass 2：修 dialer-proxy 引用——同样原地 mutate
  var validNames = {};
  for (var j = 0; j < intermediate.length; j++) {
    var p = intermediate[j];
    if (p && typeof p.name === 'string') validNames[p.name] = true;
  }
  var renameKeyCount = 0;
  for (var rk in renameMap) {
    if (Object.prototype.hasOwnProperty.call(renameMap, rk)) renameKeyCount++;
  }
  if (typeof console !== 'undefined' && console.log) {
    console.log('[shadowrocket-injector] Pass2: rename_pairs=' + renameKeyCount + ' valid_names=' + Object.keys(validNames).length);
  }

  var out = [];
  var rewroteCount = 0;
  var droppedCount = 0;
  for (var k = 0; k < intermediate.length; k++) {
    var node = intermediate[k];
    // 根因：Sub-Store v2.22.8 内部把 dialer-proxy 同步存到 underlying-proxy 字段，
    // mihomo producer 序列化时优先用 underlying-proxy 覆盖 dialer-proxy。
    // 修复：两个字段都要 mutate。
    if (!node) {
      out.push(node);
      continue;
    }
    var rawRef = (typeof node['dialer-proxy'] === 'string')
      ? node['dialer-proxy']
      : (typeof node['underlying-proxy'] === 'string' ? node['underlying-proxy'] : null);
    if (rawRef == null) {
      out.push(node);
      continue;
    }
    var newRef = (rawRef in renameMap) ? renameMap[rawRef] : rawRef;
    if (validNames[newRef]) {
      if (newRef !== rawRef) {
        node['dialer-proxy'] = newRef;
        node['underlying-proxy'] = newRef;  // 关键：覆盖内部存储字段
        rewroteCount++;
      }
      out.push(node);
      continue;
    }
    droppedCount++;
    if (typeof console !== 'undefined' && console.log) {
      console.log('[shadowrocket-injector] 丢弃 ' + node.name + '：dialer-proxy "' + rawRef + '" 找不到对应节点');
    }
  }
  if (typeof console !== 'undefined' && console.log) {
    console.log('[shadowrocket-injector] Pass2 done: rewrote=' + rewroteCount + ' dropped=' + droppedCount + ' kept=' + out.length);
  }
  return out;
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
  if (typeof console !== 'undefined' && console.log) {
    var dialerCount = 0;
    var underlyingCount = 0;
    if (Array.isArray(proxies)) {
      for (var i = 0; i < proxies.length; i++) {
        if (proxies[i] && typeof proxies[i]['dialer-proxy'] === 'string') dialerCount++;
        if (proxies[i] && typeof proxies[i]['underlying-proxy'] === 'string') underlyingCount++;
      }
    }
    console.log('[shadowrocket-injector] operator entry: target=' + targetPlatform + ' input_count=' + (Array.isArray(proxies) ? proxies.length : 'N/A') + ' input_dialer=' + dialerCount + ' input_underlying=' + underlyingCount);
  }
  const normalizedProxies = normalizeAirportProxies(proxies);
  const node = buildVpsFrontierNode();
  if (node) {
    const exists = Array.isArray(normalizedProxies) && normalizedProxies.some(function (p) {
      return p && p.name === node.name;
    });
    if (!exists) normalizedProxies.push(node);
  }
  if (typeof console !== 'undefined' && console.log) {
    var outDialer = 0;
    var outOldRefs = 0;
    for (var j = 0; j < normalizedProxies.length; j++) {
      var p = normalizedProxies[j];
      if (p && typeof p['dialer-proxy'] === 'string') {
        outDialer++;
        if (p['dialer-proxy'].indexOf('广东-') !== -1) outOldRefs++;
      }
    }
    console.log('[shadowrocket-injector] operator return: count=' + normalizedProxies.length + ' dialer_count=' + outDialer + ' OLD_refs_remaining=' + outOldRefs);
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
