/**
 * frontier-chain-skeleton
 *
 * 通用 mihomo 全 config 覆写脚本，作为 GitHub 公开仓库统一发布，供 Sparkle (Win/Mac) +
 * Sub-Store (Linux/VPS) 双端通过 jsdelivr CDN 共享引用。
 *
 * 入口约定：
 *   - 导出 globalThis.main = function main(config)，接收 mihomo 完整配置对象，返回修改后的配置
 *   - Sparkle override 兼容这种签名（旧版用 operator(proxies) 包装，新版直接吃 main(config)）
 *   - Sub-Store mihomoProfile 文件类型通过 Response Transformer 消费 response body
 *
 * 运行时参数：
 *   当前版本不再读取住宅代理供应商凭据。家宽节点全部来自 Sub-Store
 *   merged-airports 上游订阅池，客户端通过稳定的「🏡 家宽选择」selector 手选。
 *
 * 仓库：https://github.com/konbakuyomu/frontier-chain-skeleton
 * 许可：MIT（建议）
 */


const AI = {
  enabled: true,
  targetGroup: "AI服务",
};

const SSH_ROUTING = {
  targetGroup: "SSH",
  findProcessMode: "always",
  ports: [
    22,
    14514,
  ],
  processNames: [
    "ssh.exe",
    "sftp.exe",
    "scp.exe",
    "Termius.exe",
    "FinalShell.exe",
    "putty.exe",
    "plink.exe",
    "psftp.exe",
    "WinSCP.exe",
    "MobaXterm.exe",
    "Xshell.exe",
  ],
  // Do not add a broad java.exe process rule. For Java-wrapped clients such as
  // FinalShell, add a precise install-path wildcard after checking the
  // process path in the Mihomo/Sparkle connection details.
  processPathWildcards: [],
};

const RULE_MIRROR_BASE_URL = "https://link.konbakuyomu.us/rules";
const SELF_DOMAIN_GROUP_NAME = "自有域名";
const SELF_DOMAIN_SUFFIXES = ["konbakuyomu.us"];
const SELF_DOMAIN_DOH_NAMESERVERS = [
  "https://cloudflare-dns.com/dns-query",
  "https://dns.google/dns-query",
];
const DNS_BASE_REAL_IP_RULES = [
  "DOMAIN-SUFFIX,lan,real-ip",
  "DOMAIN-SUFFIX,local,real-ip",
  "DOMAIN-SUFFIX,internal,real-ip",
  "DOMAIN-SUFFIX,home.arpa,real-ip",
  "DOMAIN-SUFFIX,in-addr.arpa,real-ip",
  "DOMAIN-SUFFIX,ip6.arpa,real-ip",
  "DOMAIN-SUFFIX,msftconnecttest.com,real-ip",
  "DOMAIN-SUFFIX,msftncsi.com,real-ip",
  "DOMAIN-KEYWORD,stun,real-ip",
  "DOMAIN-SUFFIX,push.apple.com,real-ip",
  "DOMAIN-SUFFIX,apple.com,real-ip",
  "DOMAIN-SUFFIX,icloud.com,real-ip",
  "DOMAIN,localhost.ptlogin2.qq.com,real-ip",
  "DOMAIN-SUFFIX,market.xiaomi.com,real-ip",
  "DOMAIN-KEYWORD,_tcp,real-ip",
  "DOMAIN-KEYWORD,_udp,real-ip",
];

function ruleMirrorUrl(fileName) {
  return `${RULE_MIRROR_BASE_URL}/${fileName}`;
}

function buildSelfDomainNameserverPolicy() {
  const policy = {};
  for (const suffix of SELF_DOMAIN_SUFFIXES) {
    policy[suffix] = [...SELF_DOMAIN_DOH_NAMESERVERS];
    policy[`+.${suffix}`] = [...SELF_DOMAIN_DOH_NAMESERVERS];
  }
  return policy;
}

function routeRuleToFakeIpFilterRule(rule) {
  if (typeof rule !== "string") return null;
  const parts = rule.split(",").map(part => part.trim());
  if (parts.length < 3) return null;

  const type = parts[0];
  const domainRuleTypes = new Set([
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "GEOSITE",
    "RULE-SET",
  ]);
  if (!domainRuleTypes.has(type)) return null;

  const target = parts[2];
  const dnsTarget = (target === "DIRECT" || target === SELF_DOMAIN_GROUP_NAME) ? "real-ip" : "fake-ip";
  return `${type},${parts[1]},${dnsTarget}`;
}

function buildFakeIpFilterRules(config) {
  const rules = [
    ...DNS_BASE_REAL_IP_RULES,
    ...SELF_DOMAIN_SUFFIXES.map(suffix => `DOMAIN-SUFFIX,${suffix},real-ip`),
  ];
  if (Array.isArray(config.rules)) {
    for (const rule of config.rules) {
      const dnsRule = routeRuleToFakeIpFilterRule(rule);
      if (dnsRule) rules.push(dnsRule);
    }
  }
  rules.push("MATCH,fake-ip");
  return [...new Set(rules)];
}

const KNOWN_RULE_PROVIDER_MIRRORS = [
  {
    source: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/gfw.txt",
    mirrorFile: "mihomo-gfwlist-loyalsoldier.txt",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/217heidai/adblockfilters@main/rules/adblockmihomolite.yaml",
    mirrorFile: "mihomo-adblock-217heidai.yaml",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/AdditionalCDNResources.list",
    mirrorFile: "mihomo-powerfullz-additional-cdn-resources.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/AdditionalFilter.list",
    mirrorFile: "mihomo-powerfullz-additional-filter.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/Crypto.list",
    mirrorFile: "mihomo-powerfullz-crypto.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/EHentai.list",
    mirrorFile: "mihomo-powerfullz-ehentai.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/FirebaseCloudMessaging.list",
    mirrorFile: "mihomo-powerfullz-googlefcm.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/SteamFix.list",
    mirrorFile: "mihomo-powerfullz-steamfix.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/TikTok.list",
    mirrorFile: "mihomo-powerfullz-tiktok.list",
  },
  {
    source: "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@master/ruleset/Weibo.list",
    mirrorFile: "mihomo-powerfullz-weibo.list",
  },
];

function rewriteKnownRuleProviderMirrors(config) {
  const providers = config && config["rule-providers"];
  if (!providers || typeof providers !== "object") return 0;

  let rewritten = 0;
  for (const provider of Object.values(providers)) {
    if (!provider || typeof provider !== "object") continue;
    const url = String(provider.url || "");
    if (!url) continue;
    const known = KNOWN_RULE_PROVIDER_MIRRORS.find(item => url === item.source);
    if (!known) continue;
    provider.url = ruleMirrorUrl(known.mirrorFile);
    rewritten += 1;
  }
  return rewritten;
}

const UPSTREAM_MIHOMO_MAIN = (() => {
  if (typeof globalThis === "undefined" || typeof globalThis.main !== "function") return null;
  if (globalThis.__frontierSkeletonMain && globalThis.main === globalThis.__frontierSkeletonMain) return null;
  return globalThis.main;
})();


// ============================================================
// 过滤正则（公共信息，可见）
// ============================================================

const BUILTIN_RULE_TARGETS = new Set(["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"]);


// ============================================================
// 工具函数（与 Sparkle 本地版同源；不动业务逻辑）
// ============================================================

function logInfo(message) {
  console.log(`[skeleton] ${message}`);
}

function logWarn(message) {
  if (typeof console.warn === "function") {
    console.warn(`[skeleton] ${message}`);
  } else {
    console.log(`[warn] [skeleton] ${message}`);
  }
}

function uniqueList(items) {
  const out = [];
  const seen = new Set();
  for (const item of items || []) {
    const value = String(item == null ? "" : item).trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function compileFilter(mihomoFilter) {
  if (mihomoFilter.startsWith("(?i)")) {
    return new RegExp(mihomoFilter.slice(4), "i");
  }
  return new RegExp(mihomoFilter);
}

function resolveGroup(config, options) {
  const {
    label,
    explicit,
    preferred = [],
    fuzzy,
    fallback = [],
    defaultTarget = "DIRECT",
  } = options;
  const groups = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];
  const groupNames = groups.map(g => g && g.name).filter(Boolean);

  if (explicit) {
    if (groupNames.includes(explicit)) return explicit;
    logWarn(`${label} 显式目标组 "${explicit}" 不存在，改为自动检测`);
  }

  for (const name of preferred) {
    if (groupNames.includes(name)) return name;
  }

  if (fuzzy) {
    const hit = groups.find(g => g && typeof g.name === "string" && fuzzy.test(g.name));
    if (hit) return hit.name;
  }

  for (const name of fallback) {
    if (groupNames.includes(name) || BUILTIN_RULE_TARGETS.has(name)) return name;
  }

  if (groupNames.length > 0) {
    logWarn(`${label} 未找到匹配策略组，兜底使用 "${groupNames[0]}"`);
    return groupNames[0];
  }

  logWarn(`${label} 未找到任何策略组，兜底使用 ${defaultTarget}`);
  return defaultTarget;
}

function findSelectGroup(config) {
  return resolveGroup(config, {
    label: "默认代理",
    preferred: ["选择代理", "节点选择", "Proxy", "PROXY", "手动选择", "GLOBAL"],
    fuzzy: /选择代理|节点选择|Proxy|PROXY/,
  });
}

function findGoogleGroup(config, fallbackGroup) {
  return resolveGroup(config, {
    label: "Google",
    preferred: ["谷歌服务", "Google服务", "Google", "🔍 谷歌", "🔍 Google"],
    fuzzy: /谷歌|[Gg]oogle/,
    fallback: [fallbackGroup],
  });
}

function findAIGroup(config, fallbackGroup, explicitTarget) {
  return resolveGroup(config, {
    label: "AI",
    explicit: explicitTarget,
    preferred: ["AI服务", "AI", "ChatGPT", "🤖 AI"],
    fuzzy: /AI|GPT|Claude|Gemini/i,
    fallback: [fallbackGroup],
  });
}

function findSSHGroup(config, fallbackGroup) {
  return resolveGroup(config, {
    label: "SSH",
    explicit: SSH_ROUTING.targetGroup,
    preferred: ["SSH", "SSH(22端口)", "SSH 分流"],
    fuzzy: /^SSH(?:\b|[（(]|$)/i,
    fallback: [fallbackGroup],
  });
}

function buildSSHRules(sshGroup) {
  const portRules = uniqueList(SSH_ROUTING.ports)
    .filter(port => /^\d+$/.test(port))
    .map(port => `DST-PORT,${port},${sshGroup}`);
  const processNameRules = uniqueList(SSH_ROUTING.processNames)
    .map(name => `PROCESS-NAME,${name},${sshGroup}`);
  const processPathRules = uniqueList(SSH_ROUTING.processPathWildcards)
    .map(path => `PROCESS-PATH-WILDCARD,${path},${sshGroup}`);
  return [...portRules, ...processNameRules, ...processPathRules];
}

function ensureFindProcessMode(config) {
  if (!SSH_ROUTING.findProcessMode) return "";
  config["find-process-mode"] = SSH_ROUTING.findProcessMode;
  return config["find-process-mode"];
}

function getRuleTarget(rule) {
  const parts = String(rule).split(",").map(part => part.trim()).filter(Boolean);
  if (parts.length < 2) return null;
  const last = parts[parts.length - 1];
  if (last === "no-resolve" && parts.length >= 3) return parts[parts.length - 2];
  return last;
}

function validateRuleTargets(config, rules) {
  const groups = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];
  const groupNames = new Set(groups.map(g => g && g.name).filter(Boolean));
  const missing = [...new Set(rules
    .map(getRuleTarget)
    .filter(target => target && !groupNames.has(target) && !BUILTIN_RULE_TARGETS.has(target)))];
  if (missing.length > 0) {
    logWarn(`新增规则存在未知目标组：${missing.join(", ")}`);
    return false;
  }
  logInfo(`新增规则目标校验通过，共 ${rules.length} 条`);
  return true;
}

function prependUniqueRules(config, rules) {
  if (!Array.isArray(config.rules)) config.rules = [];
  const existing = new Set(config.rules.map(rule => String(rule).trim()));
  const uniqueRules = [];
  for (const rule of rules) {
    const key = String(rule).trim();
    if (!key || existing.has(key)) continue;
    existing.add(key);
    uniqueRules.push(rule);
  }
  config.rules = [...uniqueRules, ...config.rules];
  return uniqueRules;
}

function ensureRulesAtFront(config, rules) {
  if (!Array.isArray(config.rules)) config.rules = [];
  const frontRules = uniqueList(rules);
  if (frontRules.length === 0) return frontRules;

  const frontRuleSet = new Set(frontRules);
  config.rules = [
    ...frontRules,
    ...config.rules.filter(rule => !frontRuleSet.has(String(rule).trim())),
  ];
  return frontRules;
}

function insertUniqueRulesBefore(config, rules, isAnchor) {
  if (!Array.isArray(config.rules)) config.rules = [];
  const existing = new Set(config.rules.map(rule => String(rule).trim()));
  const uniqueRules = [];
  for (const rule of rules) {
    const key = String(rule).trim();
    if (!key || existing.has(key)) continue;
    existing.add(key);
    uniqueRules.push(rule);
  }
  if (uniqueRules.length === 0) return uniqueRules;

  const anchorIndex = config.rules.findIndex(rule => isAnchor(String(rule).trim()));
  const insertAt = anchorIndex >= 0 ? anchorIndex : config.rules.length;
  config.rules.splice(insertAt, 0, ...uniqueRules);
  return uniqueRules;
}

// ------------------------------------------------------------
// Shadowrocket 兼容：把 mihomo 的 `include-all: true` 组就地展开
// 成节点名字数组，并删除 include-all / filter / exclude-filter 三个
// 私有字段（Shadowrocket ≥ 2.2.x 不识别这三个字段，留着会让组没节点 + 报警）
// ------------------------------------------------------------
function expandIncludeAllGroups(config) {
  const groups = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];
  const allProxyNames = (Array.isArray(config.proxies) ? config.proxies : [])
    .map(p => p && p.name)
    .filter(name => typeof name === "string" && name.length > 0);

  let expanded = 0;
  for (const group of groups) {
    if (!group || group["include-all"] !== true) continue;
    if (group.name === "🏡 家宽选择") {
      logInfo(`保留 include-all 组 "${group.name}"，让 mihomo 客户端动态吸纳家宽候选`);
      continue;
    }

    let candidates = allProxyNames.slice();
    if (typeof group.filter === "string" && group.filter.length > 0) {
      try {
        const re = compileFilter(group.filter);
        candidates = candidates.filter(name => re.test(name));
      } catch (e) {
        logWarn(`组 "${group.name}" 的 filter 编译失败：${e && e.message}，忽略 filter`);
      }
    }
    if (typeof group["exclude-filter"] === "string" && group["exclude-filter"].length > 0) {
      try {
        const re = compileFilter(group["exclude-filter"]);
        candidates = candidates.filter(name => !re.test(name));
      } catch (e) {
        logWarn(`组 "${group.name}" 的 exclude-filter 编译失败：${e && e.message}，忽略 exclude-filter`);
      }
    }

    const existing = Array.isArray(group.proxies) ? group.proxies.slice() : [];
    const seen = new Set(existing);
    const merged = existing;
    for (const name of candidates) {
      if (seen.has(name)) continue;
      seen.add(name);
      merged.push(name);
    }
    group.proxies = merged;

    delete group["include-all"];
    delete group.filter;
    delete group["exclude-filter"];

    expanded += 1;
    logInfo(`展开 include-all 组 "${group.name}"：${merged.length} 个节点`);
  }

  if (expanded > 0) {
    logInfo(`expandIncludeAllGroups 处理完成，共展开 ${expanded} 个组`);
  }
  return config;
}

function proxyNamesFromConfig(config) {
  return (Array.isArray(config.proxies) ? config.proxies : [])
    .map(proxy => proxy && proxy.name)
    .filter(name => typeof name === "string" && name.length > 0);
}

function buildFallbackRegionGroups(proxyNames) {
  const regionDefs = [
    { name: "🇭🇰 香港节点", pattern: /🇭🇰|香港|Hong ?Kong|(?<![A-Za-z])HK(?![A-Za-z])|HKG/i },
    { name: "🇹🇼 台湾节点", pattern: /🇹🇼|台湾|台灣|Taiwan|Taipei|(?<![A-Za-z])TW(?![A-Za-z])|TPE/i },
    { name: "🇯🇵 日本节点", pattern: /🇯🇵|日本|东京|大阪|Japan|Tokyo|Osaka|(?<![A-Za-z])JP(?![A-Za-z])|NRT|HND|KIX/i },
    { name: "🇰🇷 韩国节点", pattern: /🇰🇷|韩国|韓国|首尔|Korea|Seoul|(?<![A-Za-z])KR(?![A-Za-z])|ICN/i },
    { name: "🇸🇬 新加坡节点", pattern: /🇸🇬|新加坡|狮城|Singapore|(?<![A-Za-z])SG(?![A-Za-z])/i },
    { name: "🇺🇸 美国节点", pattern: /🇺🇸|美国|美國|洛杉矶|圣何塞|纽约|United States|America|(?<![A-Za-z])US(?![A-Za-z])|USA|LAX|SJC|SFO|SEA|JFK|ORD|IAD/i },
    { name: "🇪🇺 欧洲节点", pattern: /🇬🇧|🇫🇷|🇩🇪|🇳🇱|🇨🇭|🇮🇹|🇪🇸|英国|法国|德国|荷兰|瑞士|意大利|西班牙|Europe|London|Paris|Berlin|Frankfurt|Amsterdam|(?<![A-Za-z])EU(?![A-Za-z])|(?<![A-Za-z])UK(?![A-Za-z])|(?<![A-Za-z])DE(?![A-Za-z])|(?<![A-Za-z])FR(?![A-Za-z])/i },
  ];

  const groups = [];
  for (const region of regionDefs) {
    const hits = proxyNames.filter(name => region.pattern.test(name));
    if (hits.length === 0) continue;
    groups.push({
      name: region.name,
      type: "url-test",
      proxies: hits,
      url: "http://cp.cloudflare.com/generate_204",
      interval: 300,
      tolerance: 50,
      lazy: true,
    });
  }
  return groups;
}

function buildFallbackMihomoBase(config) {
  const proxyNames = proxyNamesFromConfig(config);
  const manualProxies = proxyNames.length > 0 ? proxyNames : ["DIRECT"];
  const regionGroups = buildFallbackRegionGroups(proxyNames);
  const regionGroupNames = regionGroups.map(group => group.name);
  const primaryChoices = uniqueList([
    ...regionGroupNames,
    "自动选择",
    "故障转移",
    "手动选择",
    "DIRECT",
  ]);
  const businessChoices = uniqueList(["选择代理", "手动选择", "DIRECT"]);

  logWarn("检测到 only-proxies Mihomo 输入，使用 skeleton fallback base 生成基础代理组");
  return {
    ...config,
    "proxy-groups": [
      {
        name: "选择代理",
        type: "select",
        proxies: primaryChoices.length > 0 ? primaryChoices : ["DIRECT"],
      },
      {
        name: "手动选择",
        type: "select",
        proxies: manualProxies,
      },
      {
        name: "自动选择",
        type: "url-test",
        proxies: manualProxies,
        url: "http://cp.cloudflare.com/generate_204",
        interval: 300,
        tolerance: 50,
        lazy: true,
      },
      {
        name: "故障转移",
        type: "fallback",
        proxies: manualProxies,
        url: "http://cp.cloudflare.com/generate_204",
        interval: 300,
        lazy: true,
      },
      ...regionGroups,
      {
        name: "静态资源",
        type: "select",
        proxies: businessChoices,
      },
      {
        name: "谷歌服务",
        type: "select",
        proxies: businessChoices,
      },
      {
        name: "苹果服务",
        type: "select",
        proxies: businessChoices,
      },
      {
        name: "AI服务",
        type: "select",
        proxies: businessChoices,
      },
      {
        name: "GLOBAL",
        type: "select",
        proxies: uniqueList(["选择代理", "AI服务", "PayPal", SELF_DOMAIN_GROUP_NAME, "DIRECT"]),
      },
    ],
    "rule-providers": isConfigObject(config["rule-providers"]) ? config["rule-providers"] : {},
    rules: Array.isArray(config.rules) ? config.rules : ["MATCH,选择代理"],
  };
}

function ensureMihomoBaseConfig(config) {
  if (needsUpstreamMihomoBase(config)) {
    return buildFallbackMihomoBase(config);
  }
  return config;
}


// ============================================================
// 主入口
// ============================================================

function main(config) {
  config = ensureMihomoBaseConfig(config || {});

  // ================================================
  // ===== DNS 防泄露 + TUN 兼容性（强制覆盖）=====
  // ================================================
  config.dns = {
    enable: true,
    ipv6: true,
    "enhanced-mode": "fake-ip",
    "fake-ip-range": "198.18.0.1/16",
    "fake-ip-filter-mode": "rule",
    "fake-ip-filter": buildFakeIpFilterRules(config),
    "default-nameserver": [
      "tls://223.5.5.5",
      "tls://223.6.6.6"
    ],
    nameserver: [
      "https://cloudflare-dns.com/dns-query",
      "https://dns.google/dns-query"
    ],
    "proxy-server-nameserver": [
      "https://dns.alidns.com/dns-query",
      "https://doh.pub/dns-query"
    ],
    "direct-nameserver": [
      "https://dns.alidns.com/dns-query",
      "https://doh.pub/dns-query"
    ],
    "nameserver-policy": buildSelfDomainNameserverPolicy(),
    "direct-nameserver-follow-policy": true,
    "respect-rules": true
  };

  const selectGroup = findSelectGroup(config);
  const googleGroup = findGoogleGroup(config, selectGroup);
  const sshGroup = findSSHGroup(config, selectGroup);
  const findProcessMode = ensureFindProcessMode(config);

  const sshRules = buildSSHRules(sshGroup);
  validateRuleTargets(config, sshRules);
  const insertedSSHRules = prependUniqueRules(config, sshRules);
  logInfo(`SSH 分流规则注入：${insertedSSHRules.length} 条新增，目标组 ${sshGroup}，find-process-mode=${findProcessMode || "unchanged"}`);

  const testSiteRules = [
    `DOMAIN-SUFFIX,browserleaks.com,${selectGroup}`,
    `DOMAIN-SUFFIX,browserleaks.io,${selectGroup}`,
    `DOMAIN-SUFFIX,ipleak.net,${selectGroup}`,
    `DOMAIN-SUFFIX,dnsleaktest.com,${selectGroup}`,
    `DOMAIN-SUFFIX,browserscan.net,${selectGroup}`
  ];

  const extensionFixRules = [
    `DOMAIN-SUFFIX,infinitynewtab.com,${selectGroup}`,
    `DOMAIN,s2.googleusercontent.com,${googleGroup}`,
    `DOMAIN,s1.googleusercontent.com,${googleGroup}`,
    `DOMAIN-SUFFIX,googleusercontent.com,${googleGroup}`,
    `DOMAIN-SUFFIX,gstatic.com,${googleGroup}`
  ];

  // 注：mihomo 不支持 inline URL 形式的 RULE-SET（要求 provider 必须在 rule-providers 字典里注册）。
  // Google/GoogleFCM 走 powerfullz 的 GEOSITE,GOOGLE 即可（baseRules 已含），无需在此重复 RULE-SET。
  // Shadowrocket 端是单独的 shadowrocket.conf，不通过 main.js 生成。

  const fixedRules = [...testSiteRules, ...extensionFixRules];
  validateRuleTargets(config, fixedRules);
  const insertedFixedRules = prependUniqueRules(config, fixedRules);

  // ================================================
  // ===== AI 完整分流规则 =====
  // ================================================
  if (AI.enabled) {
    const aiGroup = findAIGroup(config, selectGroup, AI.targetGroup);
    if (aiGroup) {
      // AI-ROUTING:BEGIN
      // Generated from ai-routing-contract.json and subscription-hub/rules.json.
      const managedAiProviderNames = new Set([
        "ai-dustin",
        "ai-openai",
        "ai-claude",
        "ai-anthropic",
        "ai-xai",
        "ai-community-supplement",
        "ai-gemini",
      ]);
      const historicalBroadAiRules = new Set([
        "DOMAIN-SUFFIX,sentry.io",
        "DOMAIN-SUFFIX,statsigapi.net",
        "DOMAIN-SUFFIX,datadoghq.com",
        "DOMAIN-KEYWORD,browser-intake",
        "DOMAIN-KEYWORD,datadog",
        "DOMAIN-KEYWORD,sentry",
        "DOMAIN-KEYWORD,statsig",
        "DOMAIN-KEYWORD,sift",
        "DOMAIN-SUFFIX,intercom.io",
        "DOMAIN-SUFFIX,intercomcdn.com",
        "DOMAIN-SUFFIX,website-files.com",
        "DOMAIN-SUFFIX,challenges.cloudflare.com",
        "DOMAIN,static.cloudflareinsights.com",
        "DOMAIN-SUFFIX,host.livekit.cloud",
        "DOMAIN-SUFFIX,turn.livekit.cloud",
        "DOMAIN-SUFFIX,client-api.arkoselabs.com",
        "GEOSITE,category-ntp",
      ]);
      const negativeAiDomainControls = new Set([
        "x.com",
      ]);
      const negativeAiSuffixControls = new Set([
      ]);
      function isNegativeAiControl(parts) {
        const ruleType = parts[0];
        const ruleValue = String(parts[1] || "").toLowerCase().replace(/[.]$/, "");
        if (ruleType !== "DOMAIN" && ruleType !== "DOMAIN-SUFFIX") return false;
        if (negativeAiDomainControls.has(ruleValue)) return true;
        return [...negativeAiSuffixControls].some(control =>
          ruleValue === control || ruleValue.endsWith(`.${control}`)
        );
      }
      const aiProviders = {
        "ai-openai": {
          type: "http", behavior: "classical", format: "text",
          url: ruleMirrorUrl("mihomo-ai-openai-v2.list"),
          path: "./ruleset/ai-openai.list", interval: 86400,
          proxy: selectGroup,
        },
        "ai-anthropic": {
          type: "http", behavior: "classical", format: "text",
          url: ruleMirrorUrl("mihomo-ai-anthropic-v2.list"),
          path: "./ruleset/ai-anthropic.list", interval: 86400,
          proxy: selectGroup,
        },
        "ai-xai": {
          type: "http", behavior: "classical", format: "text",
          url: ruleMirrorUrl("mihomo-ai-xai-v2.list"),
          path: "./ruleset/ai-xai.list", interval: 86400,
          proxy: selectGroup,
        },
        "ai-community-supplement": {
          type: "http", behavior: "classical", format: "text",
          url: ruleMirrorUrl("mihomo-ai-community-supplement-v2.list"),
          path: "./ruleset/ai-community-supplement.list", interval: 86400,
          proxy: selectGroup,
        },
        "ai-gemini": {
          type: "http", behavior: "classical", format: "text",
          url: ruleMirrorUrl("mihomo-ai-gemini.list"),
          path: "./ruleset/ai-gemini.list", interval: 86400,
          proxy: selectGroup,
        },
      };
      const retainedRuleProviders = { ...(config["rule-providers"] || {}) };
      for (const providerName of managedAiProviderNames) delete retainedRuleProviders[providerName];
      config["rule-providers"] = { ...retainedRuleProviders, ...aiProviders };
      function readExtraAiApiHosts() {
        const collected = [];
        if (typeof globalThis !== "undefined" && Array.isArray(globalThis.__frontierExtraAiApiHosts)) {
          collected.push(...globalThis.__frontierExtraAiApiHosts);
        }
        try {
          if (typeof $arguments !== "undefined" && $arguments && $arguments.extra_ai_api_hosts) {
            const raw = $arguments.extra_ai_api_hosts;
            if (Array.isArray(raw)) collected.push(...raw);
            else if (typeof raw === "string" && raw.trim()) collected.push(...raw.split(/[\s,]+/));
          }
        } catch (error) {}
        const hostRe = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$/i;
        const unique = [];
        for (const item of collected) {
          const host = String(item || "").trim().toLowerCase().replace(/[.]$/, "");
          if (!hostRe.test(host) || unique.includes(host)) continue;
          unique.push(host);
        }
        return unique;
      }
      const extraAiApiHostRules = readExtraAiApiHosts().map(host => `DOMAIN,${host},${aiGroup}`);
      const aiRules = [
        // Official required baseline, then selected conditional first-party hosts.
        `DOMAIN-SUFFIX,chatgpt.com,${aiGroup}`,
        `DOMAIN-SUFFIX,openai.com,${aiGroup}`,
        `DOMAIN-SUFFIX,oaistatic.com,${aiGroup}`,
        `DOMAIN-SUFFIX,oaiusercontent.com,${aiGroup}`,
        `DOMAIN-SUFFIX,oaistatsig.com,${aiGroup}`,
        `DOMAIN,cdn.openaimerge.com,${aiGroup}`,
        `DOMAIN,api.openai.com,${aiGroup}`,
        `DOMAIN,auth.openai.com,${aiGroup}`,
        `DOMAIN-SUFFIX,anthropic.com,${aiGroup}`,
        `DOMAIN,api.anthropic.com,${aiGroup}`,
        `DOMAIN,grok.com,${aiGroup}`,
        `DOMAIN,cdn.grok.com,${aiGroup}`,
        `DOMAIN,api.x.ai,${aiGroup}`,
        `DOMAIN-SUFFIX,claude.ai,${aiGroup}`,
        `DOMAIN-SUFFIX,claude.com,${aiGroup}`,
        `DOMAIN-SUFFIX,claudeusercontent.com,${aiGroup}`,
        `DOMAIN-SUFFIX,claudemcpclient.com,${aiGroup}`,
        `DOMAIN-SUFFIX,claudemcpcontent.com,${aiGroup}`,
        `DOMAIN-SUFFIX,clau.de,${aiGroup}`,
        `DOMAIN,platform.claude.com,${aiGroup}`,
        `DOMAIN,mcp-proxy.anthropic.com,${aiGroup}`,
        `DOMAIN,bridge.claudeusercontent.com,${aiGroup}`,
        `DOMAIN,downloads.claude.ai,${aiGroup}`,
        `DOMAIN-SUFFIX,x.ai,${aiGroup}`,
        `DOMAIN,assets.grok.com,${aiGroup}`,
        `DOMAIN,grok.x.com,${aiGroup}`,
        // Preserve the existing Gemini CLI compatibility baseline.
        `DOMAIN,cloudcode-pa.googleapis.com,${aiGroup}`,
        `DOMAIN,cloudaicompanion.googleapis.com,${aiGroup}`,
        `DOMAIN-SUFFIX,generativelanguage.googleapis.com,${aiGroup}`,
        `DOMAIN-SUFFIX,aistudio.google.com,${aiGroup}`,
        `DOMAIN,claude.googleapis.com,${aiGroup}`,
        ...extraAiApiHostRules,
        // Policy-projected SJC logical providers only supplement the local baseline.
        `RULE-SET,ai-openai,${aiGroup}`,
        `RULE-SET,ai-anthropic,${aiGroup}`,
        `RULE-SET,ai-xai,${aiGroup}`,
        `RULE-SET,ai-community-supplement,${aiGroup}`,
        `RULE-SET,ai-gemini,${aiGroup}`
      ];
      const canonicalAiRules = new Set(aiRules.map(rule => String(rule).trim()));
      let removedManagedAiRules = 0;
      if (Array.isArray(config.rules)) {
        config.rules = config.rules.filter(rule => {
          const parts = String(rule).split(",").map(part => part.trim());
          if (getRuleTarget(rule) !== aiGroup) return true;
          const ruleHead = `${parts[0]},${parts[1]}`;
          const isManagedProvider = parts[0] === "RULE-SET" && managedAiProviderNames.has(parts[1]);
          const isCanonicalBaseline = canonicalAiRules.has(String(rule).trim());
          if (!isManagedProvider && !isCanonicalBaseline && !historicalBroadAiRules.has(ruleHead) && !isNegativeAiControl(parts)) return true;
          removedManagedAiRules += 1;
          return false;
        });
      }
      if (config.rules && Array.isArray(config.rules)) {
        validateRuleTargets(config, aiRules);
        const insertedAiRules = prependUniqueRules(config, aiRules);
        logInfo(`AI routing contract applied: removed=${removedManagedAiRules}, baseline=26, providers=4, inserted=${insertedAiRules.length}`);
      }
      // AI-ROUTING:END
    }
  }

  // ================================================
  // ===== 用户自定义规则（最后注入 → 最高优先级）=====
  // 唯一的"用户插槽"。新增分流规则统一在 userRules 数组内追加，不要再造新块。
  // ================================================
  {
    // 注：组挂了大图标（icon 字段），组名故意不带 emoji 前缀
    // 详见 .trellis/spec/network/proxy-group-flexibility.md §5 图标 + 命名规则
    const paypalGroupName = "PayPal";
    const selfDomainGroupName = SELF_DOMAIN_GROUP_NAME;
    const RESIDENTIAL_SELECTOR_NAME = "🏡 家宽选择";

    // Koolson/Qure 图标库 base（与 powerfullz 国家组同款，Sparkle UI 显示协调）
    const ICON_BASE = "https://cdn.jsdelivr.net/gh/Koolson/Qure@master/IconSet/Color";
    const RESIDENTIAL_SELECTOR_ICON = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/1f3e1.png";

    // ===== 区域家宽矩阵（spec §6）=====
    // 来源：cherry-pick Smart-Config-Kit/Shadowrocket.conf 行 119-152 的 9 区域 url-test 定义
    // 关键设计：filter 用 (区域).*(家宽)|(家宽).*(区域) 双向匹配，命中"美国-家宽-LA-1"或"Resi-Tokyo-01"两类命名
    // 双端一致性：Shadowrocket.conf 必须同步等价 9 区域家宽组（见 spec §6 一致性表）
    const RESIDENTIAL_PATTERN = "[Rr]esi(dential)?|[Hh]ome[-_ ]?[Ii][Pp]|[Hh]ome[-_ ]?[Bb]roadband|[Bb]roadband|[Ii][Ss][Pp]|家宽|家庭宽带|家庭住宅|住宅宽带|住宅|宽带";
    const EXCLUDE_INFO_PATTERN = "导航|剩余|套餐|到期|重置|官网|订阅|回国|回程|国内专线|地址|保底|客服|流量|距离下次|不可直连|小白不要连接";

    // 每个区域 regionPattern 直接 cherry-pick Smart-Config-Kit policy-regex-filter 的国家段
    // 顺序：从大到小（全球 → 大洲/区域聚合 → 单国），UI 排列上下文从宽到窄
    const REGION_RESIDENTIAL_GROUPS = [
      {
        name: "🏡 全球家宽",
        regionPattern: null,  // 全球家宽特殊：只匹配 RESIDENTIAL_PATTERN，不复合区域
        icon: `${ICON_BASE}/World_Map.png`,
      },
      {
        name: "🏡 香港家宽",
        regionPattern: "🇭🇰|(?<![a-zA-Z])HK(?![a-zA-Z])|Hong|hong|HongKong|hongkong|HKG|香港|深港|沪港|京港|中港|Hong Kong",
        icon: `${ICON_BASE}/Hong_Kong.png`,
      },
      {
        name: "🏡 台湾家宽",
        regionPattern: "🇹🇼|(?<![a-zA-Z])TW(?![a-zA-Z])|Taiwan|taiwan|TWN|Taipei|taipei|TPE|台湾|台灣|台北|台中|高雄|新北|桃园",
        icon: `${ICON_BASE}/Taiwan.png`,
      },
      {
        name: "🏡 日韩家宽",
        regionPattern: "🇯🇵|🇰🇷|(?<![a-zA-Z])JP(?![a-zA-Z])|Japan|japan|JPN|Tokyo|tokyo|Osaka|osaka|NRT|HND|KIX|日本|东京|大阪|横滨|名古屋|(?<![a-zA-Z])KR(?![a-zA-Z])|Korea|korea|KOR|Seoul|seoul|ICN|韩国|首尔|釜山|仁川",
        icon: `${ICON_BASE}/Japan.png`,
      },
      {
        name: "🏡 亚太家宽",
        regionPattern: "🇭🇰|🇹🇼|🇯🇵|🇰🇷|🇸🇬|🇲🇾|🇹🇭|🇻🇳|🇵🇭|🇮🇩|🇮🇳|(?<![a-zA-Z])HK(?![a-zA-Z])|(?<![a-zA-Z])TW(?![a-zA-Z])|(?<![a-zA-Z])JP(?![a-zA-Z])|(?<![a-zA-Z])KR(?![a-zA-Z])|(?<![a-zA-Z])SG(?![a-zA-Z])|(?<![a-zA-Z])MY(?![a-zA-Z])|(?<![a-zA-Z])TH(?![a-zA-Z])|(?<![a-zA-Z])VN(?![a-zA-Z])|(?<![a-zA-Z])PH(?![a-zA-Z])|(?<![a-zA-Z])ID(?![a-zA-Z])|(?<![a-zA-Z])IN(?![a-zA-Z])|Hong|Taiwan|Japan|Korea|Singapore|Malaysia|Thailand|Vietnam|Philippines|Indonesia|India|香港|台湾|日本|韩国|新加坡|狮城|马来|泰国|越南|菲律宾|印尼|印度|亚太|iplc|IEPL|专线|cn2|GIA",
        icon: `${ICON_BASE}/Asia_Map.png`,
      },
      {
        name: "🏡 美国家宽",
        regionPattern: "🇺🇸|(?<![a-zA-Z])US(?![a-zA-Z])|USA|America|america|United States|LAX|SJC|SFO|SEA|JFK|ORD|DFW|IAD|ATL|MIA|美国|洛杉矶|圣何塞|旧金山|西雅图|纽约|芝加哥|达拉斯|凤凰城|亚特兰大|迈阿密|波士顿|华盛顿|休斯顿|硅谷|弗吉尼亚|奥斯汀|拉斯维加斯",
        icon: `${ICON_BASE}/United_States_Map.png`,
      },
      {
        name: "🏡 欧洲家宽",
        regionPattern: "🇬🇧|🇫🇷|🇩🇪|🇳🇱|🇨🇭|🇮🇹|🇪🇸|🇷🇺|(?<![a-zA-Z])EU(?![a-zA-Z])|(?<![a-zA-Z])UK(?![a-zA-Z])|(?<![a-zA-Z])GB(?![a-zA-Z])|(?<![a-zA-Z])FR(?![a-zA-Z])|(?<![a-zA-Z])DE(?![a-zA-Z])|(?<![a-zA-Z])NL(?![a-zA-Z])|(?<![a-zA-Z])CH(?![a-zA-Z])|(?<![a-zA-Z])IT(?![a-zA-Z])|(?<![a-zA-Z])ES(?![a-zA-Z])|(?<![a-zA-Z])PT(?![a-zA-Z])|(?<![a-zA-Z])SE(?![a-zA-Z])|(?<![a-zA-Z])FI(?![a-zA-Z])|(?<![a-zA-Z])NO(?![a-zA-Z])|(?<![a-zA-Z])DK(?![a-zA-Z])|(?<![a-zA-Z])PL(?![a-zA-Z])|(?<![a-zA-Z])IE(?![a-zA-Z])|(?<![a-zA-Z])RU(?![a-zA-Z])|(?<![a-zA-Z])AT(?![a-zA-Z&])|(?<![a-zA-Z])BE(?![a-zA-Z])|Europe|europe|London|Paris|Berlin|Frankfurt|Amsterdam|Moscow|Zurich|Vienna|Stockholm|Madrid|Rome|Helsinki|Warsaw|Prague|LHR|CDG|FRA|AMS|SVO|ZRH|VIE|MAD|FCO|欧洲|英国|法国|德国|荷兰|瑞士|意大利|西班牙|俄罗斯|奥地利|瑞典|芬兰|挪威|丹麦|波兰|爱尔兰|伦敦|巴黎|柏林|法兰克福|阿姆斯特丹|莫斯科|苏黎世|维也纳|斯德哥尔摩|马德里|罗马|(?<![a-zA-Z])GR(?![a-zA-Z])|🇬🇷|Greece|Athens|希腊|雅典|(?<![a-zA-Z])RO(?![a-zA-Z])|🇷🇴|Romania|Bucharest|罗马尼亚|布加勒斯特|(?<![a-zA-Z])HU(?![a-zA-Z])|🇭🇺|Hungary|Budapest|匈牙利|布达佩斯|(?<![a-zA-Z])CZ(?![a-zA-Z])|🇨🇿|Czech|Portugal|Lisbon|🇵🇹|葡萄牙|里斯本|Belgium|Brussels|🇧🇪|比利时|布鲁塞尔|Ireland|Dublin|🇮🇪|爱尔兰|都柏林|Denmark|Copenhagen|🇩🇰|丹麦|哥本哈根|Norway|Oslo|🇳🇴|挪威|奥斯陆",
        icon: `${ICON_BASE}/Europe_Map.png`,
      },
      {
        name: "🏡 美洲家宽",
        regionPattern: "🇺🇸|🇨🇦|🇲🇽|🇧🇷|🇦🇷|🇨🇱|🇵🇪|🇨🇴|(?<![a-zA-Z])US(?![a-zA-Z])|USA|(?<![a-zA-Z])CA(?![a-zA-Z])|(?<![a-zA-Z])MX(?![a-zA-Z])|(?<![a-zA-Z])BR(?![a-zA-Z])|(?<![a-zA-Z])AR(?![a-zA-Z])|(?<![a-zA-Z])CL(?![a-zA-Z])|(?<![a-zA-Z])PE(?![a-zA-Z])|(?<![a-zA-Z])CO(?![a-zA-Z])|Americas|America|Canada|Mexico|Brazil|Argentina|Chile|Peru|Colombia|Toronto|Vancouver|Montreal|YYZ|YVR|GRU|GIG|EZE|美洲|加拿大|墨西哥|巴西|阿根廷|智利|秘鲁|哥伦比亚|多伦多|温哥华|蒙特利尔|圣保罗",
        icon: `${ICON_BASE}/America_Map.png`,
      },
      {
        name: "🏡 非洲家宽",
        regionPattern: "🇿🇦|🇪🇬|🇳🇬|🇰🇪|(?<![a-zA-Z])ZA(?![a-zA-Z])|(?<![a-zA-Z])EG(?![a-zA-Z])|(?<![a-zA-Z])NG(?![a-zA-Z])|(?<![a-zA-Z])KE(?![a-zA-Z])|(?<![a-zA-Z])MA(?![a-zA-Z])|(?<![a-zA-Z])TN(?![a-zA-Z])|(?<![a-zA-Z])DZ(?![a-zA-Z])|Africa|africa|South Africa|Egypt|Nigeria|Kenya|Morocco|Johannesburg|Cairo|Lagos|Nairobi|JNB|CAI|NBO|非洲|南非|埃及|尼日利亚|肯尼亚|摩洛哥|约翰内斯堡|开罗|拉各斯|内罗毕",
        icon: `${ICON_BASE}/Africa_Map.png`,
      },
    ];
    const REGION_RESIDENTIAL_NAMES = new Set(REGION_RESIDENTIAL_GROUPS.map(g => g.name));
    const withoutRegionResidentialGroups = (items) =>
      (Array.isArray(items) ? items : []).filter(name => !REGION_RESIDENTIAL_NAMES.has(name));
    const uniqueProxyList = (items) => {
      const seen = new Set();
      const out = [];
      for (const item of items) {
        if (!item || seen.has(item)) continue;
        seen.add(item);
        out.push(item);
      }
      return out;
    };

    const pgList = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];

    // 1a) upsert 9 个区域家宽 url-test 组（spec §6 区域家宽矩阵）
    //     插入位置：紧贴 powerfullz 国家组之后、业务组之前
    //     用 splice 一次性插一段，定位锚点 = 第一个 powerfullz 工具组（"AI服务" / "前置代理" / "落地节点" / "选择代理" 之一）
    //     找不到锚点就追加到末尾
    //
    //     ⚠️ hit 预检（spec §6.x 空 url-test 组陷阱）：
    //     mihomo schema 强制要求每个 proxy-group 的 proxies/use 至少有 1 个；include-all+filter
    //     在编译阶段如果命中数 = 0，组就会变成 `proxies: []` → mihomo profile-check 报
    //     `proxy group[..]: 'use' or 'proxies' missing` 让 FlClash / Sparkle 拒绝订阅。
    //     所以这里循环前先用合成 filter 跑一次预检，**没命中的组直接跳过 upsert + 跳过 GLOBAL.proxies 注入**。
    //     参考同款模式：buildFrontProxyGroups（行 161-167）的前置组预检。
    const residentialAnchorIdx = (() => {
      const candidates = ["AI服务", "前置代理", "落地节点", "选择代理"];
      for (const name of candidates) {
        const idx = pgList.findIndex(g => g && g.name === name);
        if (idx >= 0) return idx;
      }
      return pgList.length;
    })();
    const residentialExcludeRe = new RegExp(EXCLUDE_INFO_PATTERN);
    const residentialHitGroupNames = new Set();
    let residentialInsertCursor = residentialAnchorIdx;
    for (const meta of REGION_RESIDENTIAL_GROUPS) {
      if (pgList.some(g => g && g.name === meta.name)) {
        // 既存同名组：保留并视为已 hit（可能由用户手动维护或上游注入）
        residentialHitGroupNames.add(meta.name);
        continue;
      }
      const filter = meta.regionPattern == null
        ? RESIDENTIAL_PATTERN  // 全球家宽：仅匹配家宽关键词
        : `(${meta.regionPattern}).*(${RESIDENTIAL_PATTERN})|(${RESIDENTIAL_PATTERN}).*(${meta.regionPattern})`;

      // 预检：合成 filter 跑一次，统计当前订阅命中数；命中 = 0 直接跳过（避免空 url-test 组让 mihomo 启动失败）
      let filterRe;
      try {
        filterRe = new RegExp(filter);
      } catch (e) {
        logWarn(`${meta.name} filter 编译失败：${e && e.message}，跳过`);
        continue;
      }
      const hit = (config.proxies || []).some(p =>
        p && typeof p.name === "string" && filterRe.test(p.name) && !residentialExcludeRe.test(p.name)
      );
      if (!hit) {
        logInfo(`跳过 ${meta.name}：当前订阅无候选节点（避免空 url-test 组让 mihomo 启动失败）`);
        continue;
      }

      pgList.splice(residentialInsertCursor, 0, {
        name: meta.name,
        type: "url-test",
        "include-all": true,
        filter,
        "exclude-filter": EXCLUDE_INFO_PATTERN,
        url: "http://cp.cloudflare.com/generate_204",
        interval: 300,
        tolerance: 50,
        lazy: true,
        icon: meta.icon,
      });
      residentialInsertCursor += 1;
      residentialHitGroupNames.add(meta.name);
    }

    // 1b) 稳定家宽手动 selector。业务组只引用这个选择层，不引用任何具体供应商节点名。
    const residentialSelectorHead = [
      "🏡 全球家宽",
      "🏡 美国家宽",
      "🏡 日韩家宽",
      "🏡 亚太家宽",
      "🏡 香港家宽",
      "🏡 台湾家宽",
      "🏡 欧洲家宽",
      "🏡 美洲家宽",
      "🏡 非洲家宽",
    ].filter(name => residentialHitGroupNames.has(name));
    const residentialSelectorProxies = residentialSelectorHead.length > 0
      ? residentialSelectorHead
      : ["DIRECT"];
    const residentialSelector = pgList.find(g => g && g.name === RESIDENTIAL_SELECTOR_NAME);
    if (residentialSelector) {
      residentialSelector.type = "select";
      residentialSelector.proxies = [...residentialSelectorProxies];
      residentialSelector["include-all"] = true;
      residentialSelector.filter = RESIDENTIAL_PATTERN;
      residentialSelector["exclude-filter"] = EXCLUDE_INFO_PATTERN;
      residentialSelector.icon = RESIDENTIAL_SELECTOR_ICON;
    } else {
      pgList.splice(residentialInsertCursor, 0, {
        name: RESIDENTIAL_SELECTOR_NAME,
        type: "select",
        proxies: [...residentialSelectorProxies],
        "include-all": true,
        filter: RESIDENTIAL_PATTERN,
        "exclude-filter": EXCLUDE_INFO_PATTERN,
        icon: RESIDENTIAL_SELECTOR_ICON,
      });
      residentialInsertCursor += 1;
    }

    // 1c) 顶层「选择代理」也必须能手动切到家宽选择层。
    //     插在「自动选择/故障转移」之后，保留原默认入口不变。
    const primarySelectGroup = pgList.find(g => g && g.name === selectGroup && Array.isArray(g.proxies));
    if (primarySelectGroup && !primarySelectGroup.proxies.includes(RESIDENTIAL_SELECTOR_NAME)) {
      const preferredAfter = ["故障转移", "自动选择"];
      let insertAt = 0;
      for (const anchor of preferredAfter) {
        const idx = primarySelectGroup.proxies.indexOf(anchor);
        if (idx >= 0) insertAt = Math.max(insertAt, idx + 1);
      }
      primarySelectGroup.proxies.splice(insertAt, 0, RESIDENTIAL_SELECTOR_NAME);
    }

    // 2) upsert PayPal 专属选择组（UI 可切：家宽优先，附带 AI 服务整套国家/低倍率/手动选项）
    //    复用 powerfullz 生成的 "AI服务" 组的 proxies，未来 powerfullz 加新国家时 PayPal 自动跟随
    //    位置：插入到 AI服务 之后，与同类业务组（苹果服务/谷歌服务/...）聚集，UI 显示连贯
    const aiGroup = pgList.find(g => g && g.name === "AI服务");
    const aiProxiesClone = (aiGroup && Array.isArray(aiGroup.proxies)) ? [...aiGroup.proxies] : [];
    const paypalGroup = pgList.find(g => g && g.name === paypalGroupName);
    const paypalBase = aiProxiesClone.length > 0
      ? aiProxiesClone
      : [selectGroup, "DIRECT"];
    const paypalProxies = uniqueProxyList([
      RESIDENTIAL_SELECTOR_NAME,
      ...withoutRegionResidentialGroups(paypalBase),
    ]);
    if (paypalGroup) {
      paypalGroup.type = "select";
      paypalGroup.proxies = paypalProxies;
      paypalGroup.icon = `${ICON_BASE}/PayPal.png`;
    } else {
      const aiIdx = pgList.findIndex(g => g && g.name === "AI服务");
      const insertAt = aiIdx >= 0 ? aiIdx + 1 : pgList.length;
      pgList.splice(insertAt, 0, {
        name: paypalGroupName,
        type: "select",
        // PayPal 主走稳定家宽选择层；用户在该 selector 内手选具体家宽节点。
        proxies: paypalProxies,
        icon: `${ICON_BASE}/PayPal.png`,
      });
    }

    // 2a) upsert 自有域名选择组：默认直连，但 UI 里允许临时切到代理 / 家宽 / 拒绝。
    const selfDomainProxies = uniqueProxyList([
      "DIRECT",
      selectGroup,
      RESIDENTIAL_SELECTOR_NAME,
      "REJECT",
    ]);
    const selfDomainGroup = pgList.find(g => g && g.name === selfDomainGroupName);
    if (selfDomainGroup) {
      selfDomainGroup.type = "select";
      selfDomainGroup.proxies = selfDomainProxies;
      selfDomainGroup.icon = `${ICON_BASE}/Direct.png`;
    } else {
      const paypalIdx = pgList.findIndex(g => g && g.name === paypalGroupName);
      const insertAt = paypalIdx >= 0 ? paypalIdx + 1 : pgList.length;
      pgList.splice(insertAt, 0, {
        name: selfDomainGroupName,
        type: "select",
        proxies: selfDomainProxies,
        icon: `${ICON_BASE}/Direct.png`,
      });
    }
    config["proxy-groups"] = pgList;

    // 2b) 把新组注入 GLOBAL.proxies — 关键步骤
    //     FlClash 等客户端走 mihomo /proxies API 时，凭 GLOBAL.all 列出所有可见组；
    //     不在 GLOBAL.proxies 的组虽然 yaml 里存在、规则能命中，但 UI Tab 不渲染
    //     （来源：FlClash lib/common/task.dart 的 _toGroupsTask 过滤逻辑）
    const globalGroup = pgList.find(g => g && g.name === "GLOBAL");
    if (globalGroup && Array.isArray(globalGroup.proxies)) {
      globalGroup.proxies = withoutRegionResidentialGroups(globalGroup.proxies);
      const aiIdxInGlobal = globalGroup.proxies.indexOf("AI服务");
      let insertGlobalAt = aiIdxInGlobal >= 0 ? aiIdxInGlobal + 1 : globalGroup.proxies.length;
      if (!globalGroup.proxies.includes(RESIDENTIAL_SELECTOR_NAME)) {
        globalGroup.proxies.splice(insertGlobalAt, 0, RESIDENTIAL_SELECTOR_NAME);
        insertGlobalAt += 1;
      }
      // FlClash/Sparkle build the visible proxy tab from GLOBAL.all. Keep the
      // shortcut groups visible there, while business groups still only point
      // at the stable residential selector layer.
      for (const name of residentialSelectorHead) {
        if (!globalGroup.proxies.includes(name)) {
          globalGroup.proxies.splice(insertGlobalAt, 0, name);
          insertGlobalAt += 1;
        }
      }
      if (!globalGroup.proxies.includes(paypalGroupName)) {
        globalGroup.proxies.splice(insertGlobalAt, 0, paypalGroupName);
        insertGlobalAt += 1;
      }
      if (!globalGroup.proxies.includes(selfDomainGroupName)) {
        globalGroup.proxies.splice(insertGlobalAt, 0, selfDomainGroupName);
        insertGlobalAt += 1;
      }
    }

    // 3) 注册用户级 rule-providers（与 AI 同模式：mihomo 原生 .mrs，每日刷新）
    // URL 中的 `@` 必须 percent-encode，否则 mihomo HTTP provider 解析失败
    const userProviders = {
      "paypal-meta": {
        type: "http", behavior: "domain", format: "mrs",
        url: ruleMirrorUrl("mihomo-paypal-meta.mrs"),
        path: "./ruleset/paypal-meta.mrs", interval: 86400,
        proxy: selectGroup,
      },
      "paypal-cn-meta": {
        type: "http", behavior: "domain", format: "mrs",
        url: ruleMirrorUrl("mihomo-paypal-cn-meta.mrs"),
        path: "./ruleset/paypal-cn-meta.mrs", interval: 86400,
        proxy: selectGroup,
      },
    };
    config["rule-providers"] = { ...(config["rule-providers"] || {}), ...userProviders };

    // 4) 用户规则：自有域 → 自有域名组（默认直连、UI 可切），PayPal → PayPal 组
    const selfDomainRules = SELF_DOMAIN_SUFFIXES.map(suffix =>
      `DOMAIN-SUFFIX,${suffix},${selfDomainGroupName}`
    );
    const domesticDirectRules = [
      "DOMAIN-SUFFIX,qq.com,DIRECT",
      "DOMAIN-SUFFIX,weixin.qq.com,DIRECT",
      "DOMAIN-SUFFIX,wechat.com,DIRECT",
      "DOMAIN-SUFFIX,tencent.com,DIRECT",
      "DOMAIN-SUFFIX,gtimg.com,DIRECT",
      "DOMAIN-SUFFIX,gtimg.cn,DIRECT",
      "DOMAIN-SUFFIX,qpic.cn,DIRECT",
      "DOMAIN-SUFFIX,url.cn,DIRECT",
      "GEOSITE,tencent,DIRECT",
      "GEOSITE,geolocation-cn,DIRECT",
      "GEOSITE,cn,DIRECT",
    ];
    const userRules = [
      ...selfDomainRules,
      `RULE-SET,paypal-meta,${paypalGroupName}`,
      `RULE-SET,paypal-cn-meta,${paypalGroupName}`,
      // 冷启动兜底：mrs 异步拉取完成前显式命中核心域
      `DOMAIN-SUFFIX,paypal.com,${paypalGroupName}`,
      `DOMAIN-SUFFIX,paypalobjects.com,${paypalGroupName}`,
      `DOMAIN-SUFFIX,paypal-objects.com,${paypalGroupName}`,
    ];

    if (Array.isArray(config.rules)) {
      validateRuleTargets(config, userRules);
      const insertedUserRules = prependUniqueRules(config, userRules);
      validateRuleTargets(config, domesticDirectRules);
      const insertedDomesticDirectRules = insertUniqueRulesBefore(
        config,
        domesticDirectRules,
        rule => rule === "GEOIP,cn,DIRECT" || rule === "MATCH,Final" || rule.startsWith("MATCH,")
      );
      logInfo(`用户自定义规则注入：${insertedUserRules.length} 条 → ${selfDomainGroupName}/${paypalGroupName}；国内直连规则 ${insertedDomesticDirectRules.length} 条`);
    }

    // 5) 业务组镜像家宽选择层（spec §6.9）
    //    powerfullz 自动生成的业务组（AI服务/苹果服务/谷歌服务/Netflix/...）proxies 默认只含
    //    18 国 + 选择代理 + 低倍率 + 手动选择 + 直连共 21 项，**不含**我们的 🏡 *家宽 组。
    //    这里把全部 select 类型的非工具组遍历一遍；只有 AI服务 固定把稳定家宽选择放在首项。
    //    区域家宽组留在 🏡 家宽选择 内部，避免每个业务组都摊开 9 个家宽选项。
    //    其他业务组仍在末尾追加，保留其原有默认选择。
    //    具体家宽节点只出现在 🏡 家宽选择 内部，由 include-all + filter 动态吸纳。
    const TOOL_GROUPS_EXCLUDE = new Set([
      "GLOBAL",
      "选择代理", "手动选择", "自动选择", "故障转移",
      "落地节点", "低倍率节点", "静态资源", "前置代理",
      "直连", "DIRECT", "REJECT",
      RESIDENTIAL_SELECTOR_NAME,
      selfDomainGroupName,
      "广告拦截",  // spec §3 例外：拦截语义不需切代理
      // PayPal 不再排除：业务组镜像循环统一注入家宽选择层。
      // includes 检查保护去重（PayPal 头部已含 🏡 家宽选择 → 跳过）
    ]);

    const businessGroups = pgList.filter(g =>
      g && g.type === "select" &&
      typeof g.name === "string" &&
      !TOOL_GROUPS_EXCLUDE.has(g.name) &&
      Array.isArray(g.proxies)
    );

    let mirroredCount = 0;
    let aiDefaultAdjusted = false;
    for (const g of businessGroups) {
      g.proxies = withoutRegionResidentialGroups(g.proxies);
      const hasResidentialSelector = g.proxies.includes(RESIDENTIAL_SELECTOR_NAME);
      if (g.name === "AI服务") {
        const previousFirst = g.proxies[0];
        g.proxies = [
          RESIDENTIAL_SELECTOR_NAME,
          ...g.proxies.filter(name => name !== RESIDENTIAL_SELECTOR_NAME),
        ];
        aiDefaultAdjusted = !hasResidentialSelector || previousFirst !== RESIDENTIAL_SELECTOR_NAME;
      } else if (!hasResidentialSelector) {
        g.proxies.push(RESIDENTIAL_SELECTOR_NAME);
        mirroredCount++;
      }
    }
    logInfo(`业务组镜像家宽选择层完成：${businessGroups.length} 组追加，共 ${mirroredCount} 处组插入；AI服务默认家宽=${aiDefaultAdjusted}`);
  }

  // ================================================
  // ===== Shadowrocket 兼容（最后一步）=====
  // 把所有 include-all 组就地展开为节点名字数组，删除 mihomo 私有字段
  // ================================================
  const rewrittenProviderCount = rewriteKnownRuleProviderMirrors(config);
  if (rewrittenProviderCount > 0) {
    logInfo(`第三方 rule-provider URL 已切换到 SJC mirror：${rewrittenProviderCount} 条`);
  }
  expandIncludeAllGroups(config);
  const frontSSHRules = ensureRulesAtFront(config, sshRules);
  logInfo(`SSH 分流规则最终置顶：${frontSSHRules.length} 条位于规则表最前部`);
  if (config.dns && config.dns["fake-ip-filter-mode"] === "rule") {
    config.dns["fake-ip-filter"] = buildFakeIpFilterRules(config);
    logInfo(`DIRECT/自有域名 real-ip DNS 规则生成完成：${config.dns["fake-ip-filter"].length} 条`);
  }

  return config;
}


// ============================================================
// 入口暴露——兼容 Sparkle / Sub-Store mihomoProfile
// ============================================================

function isConfigObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function hasFinalMihomoShape(config) {
  return isConfigObject(config) &&
    Array.isArray(config.proxies) &&
    Array.isArray(config["proxy-groups"]) &&
    Array.isArray(config.rules) &&
    isConfigObject(config["rule-providers"]) &&
    isConfigObject(config.dns);
}

function needsUpstreamMihomoBase(config) {
  return isConfigObject(config) &&
    Array.isArray(config.proxies) &&
    !Array.isArray(config["proxy-groups"]) &&
    !Array.isArray(config.rules);
}

async function buildFinalMihomoFromProxies(proxies, label) {
  if (typeof UPSTREAM_MIHOMO_MAIN !== "function") {
    logWarn(`${label} 是节点列表但没有可用上游 main，改用 skeleton fallback base`);
    return main(buildFallbackMihomoBase({ proxies }));
  }

  const upstreamConfig = await UPSTREAM_MIHOMO_MAIN({ proxies });
  if (!isConfigObject(upstreamConfig)) {
    logWarn(`${label} 上游 main 没有返回有效 Mihomo 配置，改用 skeleton fallback base`);
    return main(buildFallbackMihomoBase({ proxies }));
  }
  return main(upstreamConfig);
}

async function finalizeMihomoConfig(config, label, options = {}) {
  if (!isConfigObject(config)) return null;
  if (needsUpstreamMihomoBase(config)) {
    const rebuilt = await buildFinalMihomoFromProxies(config.proxies, label);
    if (rebuilt) return rebuilt;
    if (options.strictUpstream) return null;
  }
  return main(config);
}

async function operator(input = [], targetPlatform, context) {
  if (
    input &&
    typeof input === "object" &&
    !Array.isArray(input) &&
    typeof input.body === "string"
  ) {
    let config = null;
    try {
      config = ProxyUtils.yaml.safeLoad(input.body);
    } catch (e) {
      logWarn(`Sub-Store response body 解析失败，保守返回原响应：${e && e.message ? e.message : e}`);
      return input;
    }
    const finalConfig = await finalizeMihomoConfig(config, "Sub-Store response body", { strictUpstream: true });
    if (!finalConfig) return input;
    input.body = ProxyUtils.yaml.safeDump(finalConfig);
    return input;
  }

  if (typeof input === "string") {
    let config = null;
    try {
      config = ProxyUtils.yaml.safeLoad(input);
    } catch (e) {
      logWarn(`Sub-Store mihomoProfile string body 解析失败，保守返回原内容：${e && e.message ? e.message : e}`);
      return input;
    }
    const finalConfig = await finalizeMihomoConfig(config, "Sub-Store mihomoProfile string body", { strictUpstream: true });
    if (!finalConfig) return input;
    return ProxyUtils.yaml.safeDump(finalConfig);
  }

  if (Array.isArray(input)) {
    const finalConfig = await buildFinalMihomoFromProxies(input, "Sub-Store proxy array");
    return finalConfig || input;
  }

  if (
    input &&
    typeof input === "object" &&
    !Array.isArray(input) &&
    input.$file &&
    input.$file.type === "mihomoProfile"
  ) {
    if (!input.$content) {
      if (typeof UPSTREAM_MIHOMO_MAIN !== "function" || typeof produceArtifact !== "function") {
        logWarn("Sub-Store mihomoProfile 缺少上游 $content，且没有可用上游 main，跳过 skeleton main");
        return input;
      }
      const upstreamInput = {
        proxies: await produceArtifact({
          type: input.$file.sourceType || "collection",
          name: input.$file.sourceName,
          platform: "mihomo",
          produceType: "internal",
          produceOpts: { "delete-underscore-fields": true },
        }),
      };
      const upstreamConfig = await UPSTREAM_MIHOMO_MAIN(upstreamInput);
      input.$content = ProxyUtils.yaml.safeDump(await main(upstreamConfig));
      return input;
    }

    let config = null;
    try {
      config = ProxyUtils.yaml.safeLoad(input.$content);
    } catch (e) {
      logWarn(`Sub-Store mihomoProfile content 解析失败，跳过 skeleton main：${e && e.message ? e.message : e}`);
      return input;
    }
    if (!config || typeof config !== "object") {
      logWarn("Sub-Store mihomoProfile content 不是有效配置对象，跳过 skeleton main");
      return input;
    }

    const finalConfig = await finalizeMihomoConfig(config, "Sub-Store mihomoProfile content", { strictUpstream: true });
    if (!finalConfig) return input;
    input.$content = ProxyUtils.yaml.safeDump(finalConfig);
    return input;
  }

  if (
    input &&
    typeof input === "object" &&
    !Array.isArray(input) &&
    (Array.isArray(input.proxies) || Array.isArray(input["proxy-groups"]) || Array.isArray(input.rules))
  ) {
    return finalizeMihomoConfig(input, "Mihomo config object");
  }

  return input;
}

async function transformFunction(res = {}, context) {
  return operator(res, undefined, context);
}

if (typeof globalThis !== "undefined") {
  globalThis.main = main;
  globalThis.transformFunction = transformFunction;
  globalThis.__frontierSkeletonMain = main;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { main, operator, transformFunction };
}
