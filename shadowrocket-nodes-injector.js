/*!
 * frontier-chain-skeleton — Sub-Store 节点清洗脚本
 *
 * 通过 Sub-Store producer + 此 Script Operator 过滤伪节点并归一化节点名
 * 入口签名: function operator(proxies, targetPlatform, context)  (Sub-Store 节点处理)
 *
 * 可选 arguments:
 *   source_prefix_map: {"subscription-name":"PREFIX"}
 *   timeout_residential_names: ["exact node name", ...]
 *
 * 安全约束:
 *   - 不写默认凭据、不硬编码供应商订阅 URL
 *   - 不注入任何自建家宽链路节点
 *   - 家宽供应只来自 Sub-Store 上游订阅池
 */

const INFO_PSEUDO_NODE_NAME_PATTERN = /(导航|剩余|套餐|到期|重置|官网|订阅|回国|回程|国内专线|地址|保底|客服|流量|距离下次|不可直连|小白不要连接)/i;
const NON_DIRECT_PROXY_PATTERN = /(不可直连|小白不要连接)/i;
const RETIRED_PROVIDER_PATTERN = /(Frontier|ScrapeGW|\[VPS[→-]>?家宽\]|\[机场[→-]>?家宽\])/i;
const RESIDENTIAL_PATTERN = /[Rr]esi(dential)?|[Hh]ome[-_ ]?[Ii][Pp]|[Hh]ome[-_ ]?[Bb]roadband|[Bb]roadband|[Ii][Ss][Pp]|家宽|家庭宽带|家庭住宅|住宅宽带|住宅|宽带/;
const DEFAULT_TIMEOUT_RESIDENTIAL_NAMES = [
  'cf加速|越南动态家宽🇻🇳',
  '越南-cf加速 动态 🇻🇳-家宽',
  'cf加速|美国备用家宽一🇺🇸',
  '美国-cf加速 备用 一🇺🇸-家宽',
  'cf加速|美国备用动态家宽三🇺🇸',
  '美国-cf加速 备用动态 三🇺🇸-家宽',
  '【5x】中转|美国备用家宽🇺🇸',
  '美国-【5x】中转 备用 🇺🇸-家宽',
  '【5x】中转|加拿大家宽🇨🇦',
  '加拿大-【5x】中转-家宽',
  '【5x】中转|韩国KT家宽',
  '韩国-【5x】中转 KT-家宽',
  '美国-密西西比州Comcast家宽-001',
  '【备用-2】美国AT&T备用家宽vless🇺🇸',
  '美国-【备用-2】 AT&T备用 🇺🇸-家宽',
  '新英国家宽🇬🇧vless',
  '英国-新英-家宽',
  '专线|尼日利亚家宽🇳🇬',
  '尼日利亚-专线-家宽',
  '尼日利亚家宽🇳🇬hy2',
  '尼日利亚-🇳🇬hy2-家宽',
];
const FLAG_PREFIX_PATTERN = /^(?:\uD83C[\uDDE6-\uDDFF]){2}\s*/;
const DEFAULT_SOURCE_PREFIX_MAP = {
  ccrui: 'L1-CCR',
  ccr: 'L1-CCR',
  kuma: 'L1-KUMA',
  bwh: 'L2-BWH',
  bandwagonhost: 'L2-BWH',
  agg: 'L1-AGG',
  aggregated: 'L1-AGG',
  'aggregated-residential': 'L1-AGG',
  residential: 'L1-AGG',
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

function parseListArg(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(function (item) { return String(item).trim(); }).filter(Boolean);
  if (typeof value === 'string') {
    try {
      var parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parseListArg(parsed);
    } catch (e) {
      /* fallback to newline/comma split */
    }
    return value.split(/\r?\n|,/).map(function (item) { return item.trim(); }).filter(Boolean);
  }
  return [];
}

function getTimeoutResidentialNameSet() {
  var names = DEFAULT_TIMEOUT_RESIDENTIAL_NAMES.slice();
  names = names.concat(parseListArg(getCred('timeout_residential_names')));
  return names.reduce(function (set, name) {
    set[name] = true;
    return set;
  }, {});
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
  if (/^L[0-9]+(?:-[A-Z0-9]+)*$/i.test(text)) return text.toUpperCase();
  if (/^(CCR|KUMA|AGG)$/i.test(text)) return 'L1-' + text.toUpperCase();
  if (/^BWH$/i.test(text)) return 'L2-BWH';
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

function isRoutePrefixedName(name) {
  return /^L[0-9]+(?:-[A-Z0-9]+)*\s*\|/.test(String(name || ''));
}

function isEvoxtNodeName(name) {
  return /^L1-EVOXT\s*\|/.test(String(name || ''));
}

function isEvoxtNode(sourcePrefix, name) {
  return sourcePrefix === 'L1-EVOXT' || isEvoxtNodeName(name);
}

function isHysteria2Proxy(proxy, name) {
  var type = String((proxy && proxy.type) || '').toLowerCase();
  return type === 'hysteria2' || /hysteria\s*2|hy2/i.test(String(name || ''));
}

function extractEvoxtHy2Port(name, proxy) {
  var port = proxy && proxy.port != null ? String(proxy.port).trim() : '';
  if (/^\d{2,5}$/.test(port)) return port;
  var match = String(name || '').match(/(?:§|\bport\b|:|\s)(\d{4,5})(?:\D|$)/i);
  return match ? match[1] : '';
}

function evoxtEntryKind(name, proxy) {
  var text = String((proxy && proxy.server) || name || '');
  if (/sslip\.io/i.test(text)) return 'SSLIP';
  if (/hiddify|konbakuyomu/i.test(text)) return '域名';
  if (/(?:^|[^\d])(?:\d{1,3}\.){3}\d{1,3}(?:$|[^\d])/.test(text)) return 'IPv4';
  return '入口';
}

function buildEvoxtHysteria2Name(name, proxy, sourcePrefix) {
  var prefix = sourcePrefix || 'L1-EVOXT';
  var kind = evoxtEntryKind(name, proxy);
  var port = extractEvoxtHy2Port(name, proxy);
  var parts = ['马来西亚', 'HY2'];
  if (kind) parts.push(kind);
  if (port) parts.push(port);
  return prefix + ' | ' + parts.join('-');
}

function routePrefixForNode(sourcePrefix, normalizedBody) {
  if (sourcePrefix !== 'L2-BWH') return sourcePrefix;
  return hasResidentialTag(normalizedBody) ? 'L2-BWH-VIRCS' : 'L2-BWH';
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

function hasResidentialTag(text) {
  return RESIDENTIAL_PATTERN.test(String(text || ''));
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
  if (!name || INFO_PSEUDO_NODE_NAME_PATTERN.test(name)) {
    return name;
  }
  const sourcePrefix = detectSourcePrefix(proxy);
  if (isEvoxtNode(sourcePrefix, name) && isHysteria2Proxy(proxy, name)) {
    return buildEvoxtHysteria2Name(name, proxy, sourcePrefix || 'L1-EVOXT');
  }
  if (isRoutePrefixedName(name)) return name;

  const region = detectExitRegion(name);
  if (!region && !sourcePrefix) return name;

  const originalIsResidential = hasResidentialTag(name);
  const provider = region ? extractProviderLabel(name, region) : cleanupProviderLabel(removeRouteTag(name));
  const routeTag = extractRouteTag(name);
  const rateLabel = extractRateLabel(name);
  var normalized = buildUnifiedName(
    sourcePrefix,
    region ? region.display : '其他',
    provider,
    routeTag,
    rateLabel,
    name
  );
  if (originalIsResidential && !hasResidentialTag(normalized)) {
    normalized = normalized + '-家宽';
  }
  if (sourcePrefix === 'L2-BWH') {
    normalized = normalized.replace(/^L2-BWH\s*\|/, routePrefixForNode(sourcePrefix, normalized) + ' |');
  }
  return normalized;
}

function isInfoPseudoNode(proxy) {
  return Boolean(proxy && typeof proxy.name === 'string' && INFO_PSEUDO_NODE_NAME_PATTERN.test(proxy.name));
}

function isNonDirectProxy(proxy) {
  return Boolean(proxy && typeof proxy.name === 'string' && NON_DIRECT_PROXY_PATTERN.test(proxy.name));
}

function isRetiredProviderProxy(proxy) {
  return Boolean(proxy && typeof proxy.name === 'string' && RETIRED_PROVIDER_PATTERN.test(proxy.name));
}

function isTimeoutResidentialNode(proxy, timeoutNames) {
  var name = proxy && typeof proxy.name === 'string' ? proxy.name : '';
  var withoutSourcePrefix = name.replace(/^[A-Z0-9]{2,8}\s*\|\s*/, '');
  return Boolean(
    proxy &&
    typeof proxy.name === 'string' &&
    hasResidentialTag(proxy.name) &&
    (timeoutNames[name] || timeoutNames[withoutSourcePrefix])
  );
}

function isUnsupportedEvoxtVlessNode(proxy, normalizedName, sourcePrefix) {
  var name = String(normalizedName || (proxy && proxy.name) || '');
  var type = String((proxy && proxy.type) || '').toLowerCase();
  if (!isEvoxtNode(sourcePrefix, name)) return false;

  // Current Hiddify Evoxt VLESS candidates are not part of the formal profile:
  // Reality fails auth in Mihomo, and the TLS/TCP fallback fails live delay.
  return type === 'vless';
}

function normalizeEvoxtHysteria2Node(proxy, normalizedName, sourcePrefix) {
  var name = String(normalizedName || (proxy && proxy.name) || '');
  var type = String((proxy && proxy.type) || '').toLowerCase();
  if (!isEvoxtNode(sourcePrefix, name) || type !== 'hysteria2') return;

  // Hiddify's native ClashMeta export omits SNI for these HY2 nodes. Keep the
  // formal Sub-Store output as close to that source as possible for FlClash.
  delete proxy.sni;
  delete proxy.servername;
}

function normalizeAirportProxies(proxies) {
  if (!Array.isArray(proxies)) return proxies;

  // 关键策略：在 Sub-Store v2.22.8 沙箱里 operator 必须**原地 mutate proxy 对象**——
  // Sub-Store 内部保留了原始对象引用，最终序列化（target=ClashMeta）从那些原始对象读字段。
  // 用 Object.assign 返回新对象的写法在测试中观察到 dialer-proxy 修改不被序列化采纳。
  var timeoutNames = getTimeoutResidentialNameSet();
  var renameMap = {};
  var intermediate = [];
  for (var i = 0; i < proxies.length; i++) {
    var proxy = proxies[i];
    if (!proxy || typeof proxy.name !== 'string') {
      intermediate.push(proxy);
      continue;
    }
    if (isInfoPseudoNode(proxy)) continue;
    if (isNonDirectProxy(proxy)) continue;
    if (isRetiredProviderProxy(proxy)) continue;
    if (isTimeoutResidentialNode(proxy, timeoutNames)) continue;
    var sourcePrefix = detectSourcePrefix(proxy);
    var normalizedName = normalizeAirportNodeName(proxy.name, proxy);
    if (isUnsupportedEvoxtVlessNode(proxy, normalizedName, sourcePrefix)) continue;
    normalizeEvoxtHysteria2Node(proxy, normalizedName, sourcePrefix);
    if (RETIRED_PROVIDER_PATTERN.test(normalizedName)) continue;
    if (
      hasResidentialTag(normalizedName) &&
      (
        timeoutNames[normalizedName] ||
        timeoutNames[normalizedName.replace(/^[A-Z0-9]{2,8}\s*\|\s*/, '')]
      )
    ) continue;
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

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    operator: operator,
    normalizeAirportNodeName: normalizeAirportNodeName,
    normalizeAirportProxies: normalizeAirportProxies,
    isInfoPseudoNode: isInfoPseudoNode,
    isNonDirectProxy: isNonDirectProxy,
    isRetiredProviderProxy: isRetiredProviderProxy,
    isTimeoutResidentialNode: isTimeoutResidentialNode,
  };
}
