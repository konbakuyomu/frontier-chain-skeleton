/**
 * frontier-chain-skeleton
 *
 * 通用 mihomo 全 config 覆写脚本，作为 GitHub 公开仓库统一发布，供 Sparkle (Win/Mac) +
 * Sub-Store (Linux/VPS) 双端通过 jsdelivr CDN 共享引用。
 *
 * 入口约定：
 *   - 导出 globalThis.main = function main(config)，接收 mihomo 完整配置对象，返回修改后的配置
 *   - Sparkle override 兼容这种签名（旧版用 operator(proxies) 包装，新版直接吃 main(config)）
 *   - Sub-Store mihomoProfile 文件类型在 backend 内部会调用 globalThis.main(config)
 *
 * 必需的 $arguments 参数（不在仓库里硬编码——全部运行时注入）：
 *   scrapegw_host / scrapegw_port / scrapegw_user / scrapegw_pass
 *   frontier_server / frontier_port / frontier_password / frontier_cipher
 *   vps_server / vps_port / vps_password / vps_cipher
 *   详见同目录 README.md
 *
 * 凭据注入两条通道（resolver 内置优先级）：
 *   1. Sub-Store 通过 URL fragment 把参数解析到 $arguments
 *   2. Sparkle 通过本地 patch 脚本（先于此脚本运行）写到 globalThis.__creds
 *
 * 仓库：https://github.com/konbakuyomu/frontier-chain-skeleton
 * 许可：MIT（建议）
 */


// ============================================================
// 凭据 resolver——优先 $arguments，其次 globalThis.__creds，最后兜底占位
// ============================================================

function getCred(key, fallback) {
  if (fallback === undefined) fallback = "<INJECT_AT_RUNTIME>";

  // 优先级 1: Sub-Store $arguments
  if (typeof $arguments !== "undefined" && $arguments && $arguments[key] != null) {
    return $arguments[key];
  }
  // 优先级 2: Sparkle 本地 patch 脚本注入到 globalThis.__creds
  if (typeof globalThis !== "undefined" && globalThis.__creds && globalThis.__creds[key] != null) {
    return globalThis.__creds[key];
  }
  // 优先级 3: 占位符——不应在生产命中，命中说明凭据通道没配
  return fallback;
}

function getCredInt(key, fallback) {
  const v = getCred(key, fallback);
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : fallback;
}

let __credsWarnedOnce = false;
function warnIfNoCreds() {
  if (__credsWarnedOnce) return;
  __credsWarnedOnce = true;
  const haveArgs = typeof $arguments !== "undefined" && $arguments && Object.keys($arguments).length > 0;
  const haveLocal = typeof globalThis !== "undefined" && globalThis.__creds && Object.keys(globalThis.__creds).length > 0;
  if (!haveArgs && !haveLocal) {
    logWarn("未检测到任何凭据注入通道（$arguments / globalThis.__creds 均空）。家宽链路将以占位符落地，节点无法连通。");
  }
}


// ============================================================
// 家宽列表（结构透明可见，敏感字段全部走 getCred）
// ============================================================

const RESIDENTIALS = [
  // #1 Frontier 美国家宽（SS，带 VPS 服务端链）
  {
    enabled: true,
    name: "Frontier",
    region: "US",
    type: "ss",
    server: getCred("frontier_server"),
    port: getCredInt("frontier_port", 1145),
    cipher: getCred("frontier_cipher", "chacha20-ietf-poly1305"),
    password: getCred("frontier_password"),
    udp: true,
    vps: {
      enabled: true,
      server: getCred("vps_server"),
      port: getCredInt("vps_port", 51388),
      cipher: getCred("vps_cipher", "chacha20-ietf-poly1305"),
      password: getCred("vps_password"),
    },
  },

  // #2 ScrapeGW 德国住宅池（SOCKS5）
  {
    enabled: true,
    name: "ScrapeGW",
    region: "DE",
    type: "socks5",
    server: getCred("scrapegw_host"),
    port: getCredInt("scrapegw_port", 6060),
    username: getCred("scrapegw_user"),
    password: getCred("scrapegw_pass"),
    udp: false,
  },
];

const AI = {
  enabled: true,
  targetGroup: "AI服务",
};


// ============================================================
// 地区元数据 / 过滤正则（公共信息，可见）
// ============================================================

const REGION_META = {
  US: { groupName: "🇺🇸 US前置", filter: "(?i)(?:^|\\|\\s*)美国-|🇺🇸|\\bUS\\b|United States" },
  DE: { groupName: "🇩🇪 DE前置", filter: "(?i)(?:^|\\|\\s*)德国-|🇩🇪|\\bDE\\b|Germany" },
  JP: { groupName: "🇯🇵 JP前置", filter: "(?i)(?:^|\\|\\s*)日本-|🇯🇵|\\bJP\\b|Japan|东京|大阪" },
  HK: { groupName: "🇭🇰 HK前置", filter: "(?i)(?:^|\\|\\s*)香港-|🇭🇰|\\bHK\\b|Hong ?Kong" },
  SG: { groupName: "🇸🇬 SG前置", filter: "(?i)(?:^|\\|\\s*)新加坡-|🇸🇬|\\bSG\\b|Singapore" },
  UK: { groupName: "🇬🇧 UK前置", filter: "(?i)(?:^|\\|\\s*)英国-|🇬🇧|\\bUK\\b|\\bGB\\b|United Kingdom|伦敦" },
};

const EXCLUDE_FILTER =
  "(?i)家宽|链式|VPS|落地|下载|Download|剩余|套餐|距离|0\\.01x";

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

function compileFilter(mihomoFilter) {
  if (mihomoFilter.startsWith("(?i)")) {
    return new RegExp(mihomoFilter.slice(4), "i");
  }
  return new RegExp(mihomoFilter);
}

function buildFrontProxyGroups(config, usedRegions) {
  const groups = [];
  const excludeRe = compileFilter(EXCLUDE_FILTER);
  for (const region of usedRegions) {
    const meta = REGION_META[region];
    if (!meta) {
      logWarn(`region "${region}" 未在 REGION_META 中定义，跳过`);
      continue;
    }
    const regionRe = compileFilter(meta.filter);
    const hit = (config.proxies || []).some(p =>
      regionRe.test(p.name) && !excludeRe.test(p.name)
    );
    if (!hit) {
      logInfo(`跳过 ${region}：当前订阅无可用 ${meta.groupName} 候选节点`);
      continue;
    }
    groups.push({
      name: meta.groupName,
      type: "url-test",
      "include-all": true,
      filter: meta.filter,
      "exclude-filter": EXCLUDE_FILTER,
      url: "https://cp.cloudflare.com/generate_204",
      interval: 300,
      tolerance: 50,
      lazy: true,
      // 与 powerfullz "前置代理" 组同款 Koolson/Qure 图标，Sparkle UI 显示更协调
      icon: "https://cdn.jsdelivr.net/gh/Koolson/Qure@master/IconSet/Color/Area.png",
    });
  }
  return groups;
}

function buildResidentialNode(r, regionGroupName) {
  const base = {
    name: `🏠 [机场→家宽] ${r.name}`,
    type: r.type,
    server: r.server,
    port: r.port,
    udp: r.udp,
  };
  if (r.type === "ss") {
    Object.assign(base, { cipher: r.cipher, password: r.password });
  } else if (r.type === "socks5" || r.type === "http") {
    Object.assign(base, { username: r.username, password: r.password });
    if (r.tls) base.tls = true;
  }
  if (regionGroupName) base["dialer-proxy"] = regionGroupName;
  return base;
}

function buildVpsNode(r) {
  if (r.type !== "ss" || !r.vps?.enabled) return null;
  return {
    name: `🏠 [VPS→家宽] ${r.name}`,
    type: "ss",
    server: r.vps.server,
    port: r.vps.port,
    cipher: r.vps.cipher,
    password: r.vps.password,
    udp: true,
  };
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


// ============================================================
// 主入口
// ============================================================

function main(config) {
  warnIfNoCreds();

  // 1. 收集启用的家宽与用到的地区
  const enabled = RESIDENTIALS.filter(r => r && r.enabled);
  const usedRegions = [...new Set(enabled.map(r => r.region))];

  // 2. 按地区生成 url-test 前置组（带预检）
  const frontGroups = buildFrontProxyGroups(config, usedRegions);
  const existingGroupNames = new Set(frontGroups.map(g => g.name));

  // 3. 生成家宽节点
  // 去重：当上游订阅来自 VPS Sub-Store（Collection `merged-airports` 的 Script Operator 已注入
  // `🏠 [VPS→家宽] Frontier`）时，本地不再重复造同名 VPS 节点。机场→家宽链节点 Sub-Store 不造，
  // 本地继续负责。
  const upstreamProxyNames = new Set(
    (config.proxies || []).map(p => p && p.name).filter(Boolean)
  );
  const myNodes = [];
  for (const r of enabled) {
    const vpsNode = buildVpsNode(r);
    if (vpsNode) {
      if (upstreamProxyNames.has(vpsNode.name)) {
        logInfo(`${r.name} 的 VPS 节点已由上游 Sub-Store 注入，本地跳过`);
      } else {
        myNodes.push(vpsNode);
      }
    }

    const groupName = REGION_META[r.region]?.groupName;
    if (existingGroupNames.has(groupName)) {
      myNodes.push(buildResidentialNode(r, groupName));
    } else if (r.vps?.enabled && r.type === "ss") {
      logInfo(`${r.name} 的机场链式节点被跳过（无 ${r.region} 前置候选），但 VPS 节点已注入`);
    } else {
      logWarn(`${r.name} 无法注入：${r.region} 前置组不存在且无 VPS 兜底`);
    }
  }

  // 4. 合并进 config
  if (myNodes.length > 0) {
    config.proxies = [...(config.proxies || []), ...myNodes];
  }
  if (frontGroups.length > 0) {
    config["proxy-groups"] = [...(config["proxy-groups"] || []), ...frontGroups];
  }

  // ================================================
  // ===== DNS 防泄露 + TUN 兼容性（强制覆盖）=====
  // ================================================
  config.dns = {
    enable: true,
    ipv6: true,
    "enhanced-mode": "fake-ip",
    "fake-ip-range": "198.18.0.1/16",
    "fake-ip-filter-mode": "blacklist",
    "fake-ip-filter": [
      "+.lan",
      "+.local",
      "+.internal",
      "+.home.arpa",
      "*.in-addr.arpa",
      "*.ip6.arpa",
      "+.msftconnecttest.com",
      "+.msftncsi.com",
      "*.stun.*.*",
      "*.stun.*.*.*",
      "+.stun.*",
      "+.push.apple.com",
      "+.apple.com",
      "+.icloud.com",
      "localhost.ptlogin2.qq.com",
      "+.market.xiaomi.com",
      "*.lan",
      "*.local",
      "*._tcp.*",
      "*._udp.*"
    ],
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
    "respect-rules": true
  };

  const selectGroup = findSelectGroup(config);
  const googleGroup = findGoogleGroup(config, selectGroup);

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
      // mihomo RULE-SET 必须引用 rule-providers 字典里注册的 provider name，
      // 不支持 inline URL（实测 mihomo profile-check 会报 "rule set <url> not found"）。
      // 我们注册 4 个 AI providers（dustin 全集 + blackmatrix7 三家拆分），让规则集提前异步拉。
      const aiProviders = {
        "ai-dustin": {
          type: "http", behavior: "domain", format: "mrs",
          url: "https://github.com/DustinWin/ruleset_geodata/releases/download/mihomo-ruleset/ai.mrs",
          path: "./ruleset/ai-dustin.mrs", interval: 86400,
          proxy: selectGroup
        },
        "ai-openai": {
          type: "http", behavior: "classical", format: "text",
          url: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/OpenAI/OpenAI.list",
          path: "./ruleset/ai-openai.list", interval: 86400,
          proxy: selectGroup
        },
        "ai-claude": {
          type: "http", behavior: "classical", format: "text",
          url: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Claude/Claude.list",
          path: "./ruleset/ai-claude.list", interval: 86400,
          proxy: selectGroup
        },
        "ai-gemini": {
          type: "http", behavior: "classical", format: "text",
          url: "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Gemini/Gemini.list",
          path: "./ruleset/ai-gemini.list", interval: 86400,
          proxy: selectGroup
        },
      };
      config["rule-providers"] = { ...(config["rule-providers"] || {}), ...aiProviders };

      const aiRules = [
        // 层1：远程 rule-providers（mihomo 标准写法）
        `RULE-SET,ai-dustin,${aiGroup}`,
        `RULE-SET,ai-openai,${aiGroup}`,
        `RULE-SET,ai-claude,${aiGroup}`,
        `RULE-SET,ai-gemini,${aiGroup}`,

        // 2a：Anthropic 核心域名
        `DOMAIN-SUFFIX,anthropic.com,${aiGroup}`,
        `DOMAIN-SUFFIX,claude.ai,${aiGroup}`,
        `DOMAIN-SUFFIX,claude.com,${aiGroup}`,
        `DOMAIN-SUFFIX,clau.de,${aiGroup}`,
        `DOMAIN-SUFFIX,claudemcpclient.com,${aiGroup}`,
        `DOMAIN-SUFFIX,claudeusercontent.com,${aiGroup}`,

        // 2b：Anthropic CDN / 基础设施
        `DOMAIN,cdn.anthropic.com,${aiGroup}`,
        `DOMAIN-SUFFIX,anthropic.com.cdn.cloudflare.net,${aiGroup}`,
        `DOMAIN,servd-anthropic-website.b-cdn.net,${aiGroup}`,
        `DOMAIN-SUFFIX,website-files.com,${aiGroup}`,

        // 2c：Anthropic 认证 / 内容
        `DOMAIN,anthropic.auth0.com,${aiGroup}`,
        `DOMAIN,anthropic-com.ghost.io,${aiGroup}`,

        // 2d：监控 / 遥测 / 反欺诈
        `DOMAIN-SUFFIX,sentry.io,${aiGroup}`,
        `DOMAIN-SUFFIX,statsigapi.net,${aiGroup}`,
        `DOMAIN-SUFFIX,datadoghq.com,${aiGroup}`,
        `DOMAIN-KEYWORD,browser-intake,${aiGroup}`,
        `DOMAIN-KEYWORD,datadog,${aiGroup}`,
        `DOMAIN-KEYWORD,sentry,${aiGroup}`,
        `DOMAIN-KEYWORD,statsig,${aiGroup}`,
        `DOMAIN-KEYWORD,sift,${aiGroup}`,

        // 2e：widget / 第三方嵌入
        `DOMAIN-SUFFIX,intercom.io,${aiGroup}`,
        `DOMAIN-SUFFIX,intercomcdn.com,${aiGroup}`,
        `DOMAIN,cdn.x.anthropic.com,${aiGroup}`,
        `DOMAIN,cdn.usefathom.com,${aiGroup}`,

        // 2f：Anthropic ASN / IP 段兜底
        // 注：以下 3 条 IP 规则在 Shadowrocket 端可能失效（IP-ASN 需 ASN 库；IP-CIDR 通常 OK），保留作 Sparkle 端兜底
        `IP-CIDR,160.79.104.0/21,${aiGroup},no-resolve`,
        `IP-CIDR6,2607:6bc0::/32,${aiGroup},no-resolve`,
        `IP-ASN,399358,${aiGroup},no-resolve`,

        // 2g：Gemini CLI 冷启动兜底
        `DOMAIN,cloudcode-pa.googleapis.com,${aiGroup}`,
        `DOMAIN,cloudaicompanion.googleapis.com,${aiGroup}`,
        `DOMAIN-SUFFIX,generativelanguage.googleapis.com,${aiGroup}`,
        `DOMAIN-SUFFIX,aistudio.google.com,${aiGroup}`,

        // 2h：OpenAI / Codex 冷启动兜底
        `DOMAIN-SUFFIX,openai.com,${aiGroup}`,
        `DOMAIN-SUFFIX,chatgpt.com,${aiGroup}`,
        `DOMAIN-SUFFIX,oaistatic.com,${aiGroup}`,
        `DOMAIN-SUFFIX,oaiusercontent.com,${aiGroup}`,

        // 2i：NTP 时区检测
        `GEOSITE,category-ntp,${aiGroup}`,

        // 2j：OpenAI / Codex 显式核心域（已被 +.openai.com 覆盖；显式 DOMAIN 比 mrs 拉取早一拍生效）
        `DOMAIN,auth.openai.com,${aiGroup}`,
        `DOMAIN,auth0.openai.com,${aiGroup}`,
        `DOMAIN,api.openai.com,${aiGroup}`,
        `DOMAIN,platform.openai.com,${aiGroup}`,
        `DOMAIN,developers.openai.com,${aiGroup}`,
        `DOMAIN,cdn.openai.com,${aiGroup}`,
        `DOMAIN,sip.api.openai.com,${aiGroup}`,
        `DOMAIN,videos.openai.com,${aiGroup}`,

        // 2k：Sora / OpenAI 收购的独立域（DustinWin mrs 含；本地兜底）
        `DOMAIN-SUFFIX,sora.com,${aiGroup}`,
        `DOMAIN-SUFFIX,chat.com,${aiGroup}`,

        // 2l：Advanced Voice / 语音对话（OpenAI 在 LiveKit Cloud 上的专属项目子域）
        `DOMAIN-SUFFIX,chatgpt.livekit.cloud,${aiGroup}`,
        `DOMAIN-SUFFIX,host.livekit.cloud,${aiGroup}`,
        `DOMAIN-SUFFIX,turn.livekit.cloud,${aiGroup}`,

        // 2m：Arkose Labs FunCaptcha（漏过会触发账号风控 / challenge 死循环）
        `DOMAIN,openai-api.arkoselabs.com,${aiGroup}`,
        `DOMAIN-SUFFIX,client-api.arkoselabs.com,${aiGroup}`,

        // 2n：Cloudflare Turnstile（共享租户但必须跟 ChatGPT 走代理，两源都没收）
        `DOMAIN-SUFFIX,challenges.cloudflare.com,${aiGroup}`,
        `DOMAIN,static.cloudflareinsights.com,${aiGroup}`,

        // 2o：OpenAI 专属 Azure CDN / 存储桶（hard-route，零污染）
        `DOMAIN,openaicom-api-bdcpf8c6d2e9atf6.z01.azurefd.net,${aiGroup}`,
        `DOMAIN,openaicomproductionae4b.blob.core.windows.net,${aiGroup}`,
        `DOMAIN,production-openaicom-storage.azureedge.net,${aiGroup}`,
        `DOMAIN-SUFFIX,openaiapi-site.azureedge.net,${aiGroup}`,
        `DOMAIN-SUFFIX,openaicom.imgix.net,${aiGroup}`,

        // 2p：OpenAI 实验/反欺诈具体子域（关键词 statsig/datadog 已兜底，这里加快命中）
        `DOMAIN-SUFFIX,featuregates.org,${aiGroup}`,
        `DOMAIN-SUFFIX,events.statsigapi.net,${aiGroup}`,
        `DOMAIN,browser-intake-datadoghq.com,${aiGroup}`,
      ];

      if (config.rules && Array.isArray(config.rules)) {
        validateRuleTargets(config, aiRules);
        const insertedAiRules = prependUniqueRules(config, aiRules);
        logInfo(`策略组解析：select="${selectGroup}", google="${googleGroup}", ai="${aiGroup}"；新增规则 fixed=${insertedFixedRules.length}, ai=${insertedAiRules.length}`);
      }
    }
  }

  // ================================================
  // ===== 用户自定义规则（最后注入 → 最高优先级）=====
  // 唯一的"用户插槽"。新增分流规则统一在 userRules 数组内追加，不要再造新块。
  // ================================================
  {
    const residentialNodeName = "🏠 [VPS→家宽] Frontier";
    // 注：以下两个组挂了大图标（icon 字段），组名故意不带 emoji 前缀
    // 详见 .trellis/spec/network/proxy-group-flexibility.md §5 图标 + 命名规则
    const residentialGroupName = "家宽-Frontier";
    const paypalGroupName = "PayPal";

    // Koolson/Qure 图标库 base（与 powerfullz 国家组同款，Sparkle UI 显示协调）
    const ICON_BASE = "https://cdn.jsdelivr.net/gh/Koolson/Qure@master/IconSet/Color";

    // ===== 区域家宽矩阵（spec §6）=====
    // 来源：cherry-pick Smart-Config-Kit/Shadowrocket.conf 行 119-152 的 9 区域 url-test 定义
    // 关键设计：filter 用 (区域).*(家宽)|(家宽).*(区域) 双向匹配，命中"美国-家宽-LA-1"或"Resi-Tokyo-01"两类命名
    // 双端一致性：Shadowrocket.conf 必须同步等价 9 区域家宽组（见 spec §6 一致性表）
    const RESIDENTIAL_PATTERN = "[Rr]esi(dential)?|[Hh]ome[-_ ]?[Ii][Pp]|[Hh]ome[-_ ]?[Bb]roadband|[Bb]roadband|[Ii][Ss][Pp]|家宽|家庭宽带|家庭住宅|住宅宽带|住宅|宽带";
    const EXCLUDE_INFO_PATTERN = "导航|剩余|套餐|到期|重置|官网|订阅|回国|回程|国内专线";

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
        regionPattern: "🇭🇰|HK|Hong|hong|HongKong|hongkong|HKG|香港|深港|沪港|京港|中港|Hong Kong",
        icon: `${ICON_BASE}/Hong_Kong.png`,
      },
      {
        name: "🏡 台湾家宽",
        regionPattern: "🇹🇼|TW|Taiwan|taiwan|TWN|Taipei|taipei|TPE|台湾|台灣|台北|台中|高雄|新北|桃园",
        icon: `${ICON_BASE}/Taiwan.png`,
      },
      {
        name: "🏡 日韩家宽",
        regionPattern: "🇯🇵|🇰🇷|JP|Japan|japan|JPN|Tokyo|tokyo|Osaka|osaka|NRT|HND|KIX|日本|东京|大阪|横滨|名古屋|KR|Korea|korea|KOR|Seoul|seoul|ICN|韩国|首尔|釜山|仁川",
        icon: `${ICON_BASE}/Japan.png`,
      },
      {
        name: "🏡 亚太家宽",
        regionPattern: "🇭🇰|🇹🇼|🇯🇵|🇰🇷|🇸🇬|🇲🇾|🇹🇭|🇻🇳|🇵🇭|🇮🇩|🇮🇳|HK|TW|JP|KR|SG|MY|TH|VN|PH|ID|IN|Hong|Taiwan|Japan|Korea|Singapore|Malaysia|Thailand|Vietnam|Philippines|Indonesia|India|香港|台湾|日本|韩国|新加坡|狮城|马来|泰国|越南|菲律宾|印尼|印度|亚太|iplc|IEPL|专线|cn2|GIA",
        icon: `${ICON_BASE}/Asia_Map.png`,
      },
      {
        name: "🏡 美国家宽",
        regionPattern: "🇺🇸|(?<![a-zA-Z])US(?![a-zA-Z])|USA|America|america|United States|LAX|SJC|SFO|SEA|JFK|ORD|DFW|IAD|ATL|MIA|美国|洛杉矶|圣何塞|旧金山|西雅图|纽约|芝加哥|达拉斯|凤凰城|亚特兰大|迈阿密|波士顿|华盛顿|休斯顿|硅谷|弗吉尼亚|奥斯汀|拉斯维加斯",
        icon: `${ICON_BASE}/United_States_Map.png`,
      },
      {
        name: "🏡 欧洲家宽",
        regionPattern: "🇬🇧|🇫🇷|🇩🇪|🇳🇱|🇨🇭|🇮🇹|🇪🇸|🇷🇺|EU|UK|GB|FR|DE|NL|CH|IT|ES|PT|(?<![a-zA-Z])SE(?![a-zA-Z])|FI|NO|DK|(?<![a-zA-Z])PL(?![a-zA-Z])|IE|RU|AT|BE|Europe|europe|London|Paris|Berlin|Frankfurt|Amsterdam|Moscow|Zurich|Vienna|Stockholm|Madrid|Rome|Helsinki|Warsaw|Prague|LHR|CDG|FRA|AMS|SVO|ZRH|VIE|MAD|FCO|欧洲|英国|法国|德国|荷兰|瑞士|意大利|西班牙|俄罗斯|奥地利|瑞典|芬兰|挪威|丹麦|波兰|爱尔兰|伦敦|巴黎|柏林|法兰克福|阿姆斯特丹|莫斯科|苏黎世|维也纳|斯德哥尔摩|马德里|罗马|(?<![a-zA-Z])GR(?![a-zA-Z])|🇬🇷|Greece|Athens|希腊|雅典|(?<![a-zA-Z])RO(?![a-zA-Z])|🇷🇴|Romania|Bucharest|罗马尼亚|布加勒斯特|(?<![a-zA-Z])HU(?![a-zA-Z])|🇭🇺|Hungary|Budapest|匈牙利|布达佩斯|(?<![a-zA-Z])CZ(?![a-zA-Z])|🇨🇿|Czech|Portugal|Lisbon|🇵🇹|葡萄牙|里斯本|Belgium|Brussels|🇧🇪|比利时|布鲁塞尔|Ireland|Dublin|🇮🇪|爱尔兰|都柏林|Denmark|Copenhagen|🇩🇰|丹麦|哥本哈根|Norway|Oslo|🇳🇴|挪威|奥斯陆",
        icon: `${ICON_BASE}/Europe_Map.png`,
      },
      {
        name: "🏡 美洲家宽",
        regionPattern: "🇺🇸|🇨🇦|🇲🇽|🇧🇷|🇦🇷|🇨🇱|🇵🇪|🇨🇴|(?<![a-zA-Z])US(?![a-zA-Z])|USA|CA|MX|BR|AR|CL|PE|CO|Americas|America|Canada|Mexico|Brazil|Argentina|Chile|Peru|Colombia|Toronto|Vancouver|Montreal|YYZ|YVR|GRU|GIG|EZE|美洲|加拿大|墨西哥|巴西|阿根廷|智利|秘鲁|哥伦比亚|多伦多|温哥华|蒙特利尔|圣保罗",
        icon: `${ICON_BASE}/America_Map.png`,
      },
      {
        name: "🏡 非洲家宽",
        regionPattern: "🇿🇦|🇪🇬|🇳🇬|🇰🇪|ZA|EG|NG|KE|MA|TN|DZ|Africa|africa|South Africa|Egypt|Nigeria|Kenya|Morocco|Johannesburg|Cairo|Lagos|Nairobi|JNB|CAI|NBO|非洲|南非|埃及|尼日利亚|肯尼亚|摩洛哥|约翰内斯堡|开罗|拉各斯|内罗毕",
        icon: `${ICON_BASE}/Africa_Map.png`,
      },
    ];

    const pgList = Array.isArray(config["proxy-groups"]) ? config["proxy-groups"] : [];

    // 1a) upsert 9 个区域家宽 url-test 组（spec §6 区域家宽矩阵）
    //     插入位置：紧贴 powerfullz 国家组之后、家宽-Frontier 之前
    //     用 splice 一次性插一段，定位锚点 = 第一个 powerfullz 工具组（"AI服务" / "前置代理" / "落地节点" / "选择代理" 之一）
    //     找不到锚点就追加到末尾
    const residentialAnchorIdx = (() => {
      const candidates = ["AI服务", "前置代理", "落地节点", "选择代理"];
      for (const name of candidates) {
        const idx = pgList.findIndex(g => g && g.name === name);
        if (idx >= 0) return idx;
      }
      return pgList.length;
    })();
    let residentialInsertCursor = residentialAnchorIdx;
    for (const meta of REGION_RESIDENTIAL_GROUPS) {
      if (pgList.some(g => g && g.name === meta.name)) continue;
      const filter = meta.regionPattern == null
        ? RESIDENTIAL_PATTERN  // 全球家宽：仅匹配家宽关键词
        : `(${meta.regionPattern}).*(${RESIDENTIAL_PATTERN})|(${RESIDENTIAL_PATTERN}).*(${meta.regionPattern})`;
      pgList.splice(residentialInsertCursor, 0, {
        name: meta.name,
        type: "url-test",
        "include-all": true,
        filter,
        "exclude-filter": EXCLUDE_INFO_PATTERN,
        url: "https://cp.cloudflare.com/generate_204",
        interval: 300,
        tolerance: 50,
        lazy: true,
        icon: meta.icon,
      });
      residentialInsertCursor += 1;
    }

    // 1b) upsert 通用家宽路由器（reusable，未来其他要走家宽的规则也能复用）
    if (!pgList.some(g => g && g.name === residentialGroupName)) {
      pgList.push({
        name: residentialGroupName,
        type: "select",
        proxies: [residentialNodeName, "DIRECT"],
        icon: `${ICON_BASE}/Airport.png`,  // 与 powerfullz "落地节点" 同款
      });
    }

    // 2) upsert PayPal 专属选择组（UI 可切：家宽优先，附带 AI 服务整套国家/低倍率/手动选项）
    //    复用 powerfullz 生成的 "AI服务" 组的 proxies，未来 powerfullz 加新国家时 PayPal 自动跟随
    //    位置：插入到 AI服务 之后，与同类业务组（苹果服务/谷歌服务/...）聚集，UI 显示连贯
    const aiGroup = pgList.find(g => g && g.name === "AI服务");
    const aiProxiesClone = (aiGroup && Array.isArray(aiGroup.proxies)) ? [...aiGroup.proxies] : [];
    if (!pgList.some(g => g && g.name === paypalGroupName)) {
      const aiIdx = pgList.findIndex(g => g && g.name === "AI服务");
      const insertAt = aiIdx >= 0 ? aiIdx + 1 : pgList.length;
      // PayPal 主走美国家宽（与 spec §6 默认锁定语义一致）：
      // 顺序 = [家宽-Frontier, VPS→家宽 Frontier, 🏡 美国家宽] + AI服务 完整面板
      const paypalHead = [residentialGroupName, residentialNodeName, "🏡 美国家宽"];
      pgList.splice(insertAt, 0, {
        name: paypalGroupName,
        type: "select",
        proxies: aiProxiesClone.length > 0
          ? [...paypalHead, ...aiProxiesClone]
          : [...paypalHead, selectGroup, "DIRECT"],
        icon: `${ICON_BASE}/PayPal.png`,
      });
    }
    config["proxy-groups"] = pgList;

    // 2b) 把新组注入 GLOBAL.proxies — 关键步骤
    //     FlClash 等客户端走 mihomo /proxies API 时，凭 GLOBAL.all 列出所有可见组；
    //     不在 GLOBAL.proxies 的组虽然 yaml 里存在、规则能命中，但 UI Tab 不渲染
    //     （来源：FlClash lib/common/task.dart 的 _toGroupsTask 过滤逻辑）
    const globalGroup = pgList.find(g => g && g.name === "GLOBAL");
    if (globalGroup && Array.isArray(globalGroup.proxies)) {
      const aiIdxInGlobal = globalGroup.proxies.indexOf("AI服务");
      let insertGlobalAt = aiIdxInGlobal >= 0 ? aiIdxInGlobal + 1 : globalGroup.proxies.length;
      if (!globalGroup.proxies.includes(paypalGroupName)) {
        globalGroup.proxies.splice(insertGlobalAt, 0, paypalGroupName);
        insertGlobalAt += 1;
      }
      // 9 区域家宽组紧贴 PayPal 之后注入 GLOBAL.proxies（spec §4a：UI 显示必需）
      for (const meta of REGION_RESIDENTIAL_GROUPS) {
        if (globalGroup.proxies.includes(meta.name)) continue;
        globalGroup.proxies.splice(insertGlobalAt, 0, meta.name);
        insertGlobalAt += 1;
      }
      if (!globalGroup.proxies.includes(residentialGroupName)) {
        // 家宽-Frontier 是工具组件，放 GLOBAL.proxies 末尾即可
        globalGroup.proxies.push(residentialGroupName);
      }
    }

    // 3) 注册用户级 rule-providers（与 AI 同模式：mihomo 原生 .mrs，每日刷新）
    // URL 中的 `@` 必须 percent-encode，否则 mihomo HTTP provider 解析失败
    const userProviders = {
      "paypal-meta": {
        type: "http", behavior: "domain", format: "mrs",
        url: "https://github.com/MetaCubeX/meta-rules-dat/raw/meta/geo/geosite/paypal.mrs",
        path: "./ruleset/paypal-meta.mrs", interval: 86400,
        proxy: selectGroup,
      },
      "paypal-cn-meta": {
        type: "http", behavior: "domain", format: "mrs",
        url: "https://github.com/MetaCubeX/meta-rules-dat/raw/meta/geo/geosite/paypal%40cn.mrs",
        path: "./ruleset/paypal-cn-meta.mrs", interval: 86400,
        proxy: selectGroup,
      },
    };
    config["rule-providers"] = { ...(config["rule-providers"] || {}), ...userProviders };

    // 4) 用户规则：PayPal → 💵 PayPal 组（UI 可切），自有域 → DIRECT
    const userRules = [
      `RULE-SET,paypal-meta,${paypalGroupName}`,
      `RULE-SET,paypal-cn-meta,${paypalGroupName}`,
      // 冷启动兜底：mrs 异步拉取完成前显式命中核心域
      `DOMAIN-SUFFIX,paypal.com,${paypalGroupName}`,
      `DOMAIN-SUFFIX,paypalobjects.com,${paypalGroupName}`,
      `DOMAIN-SUFFIX,paypal-objects.com,${paypalGroupName}`,
      // 自有域 → 直连
      `DOMAIN-SUFFIX,konbakuyomu.us,DIRECT`,
    ];

    if (Array.isArray(config.rules)) {
      validateRuleTargets(config, userRules);
      const insertedUserRules = prependUniqueRules(config, userRules);
      logInfo(`用户自定义规则注入：${insertedUserRules.length} 条 → ${paypalGroupName}/DIRECT`);
    }
  }

  // ================================================
  // ===== Shadowrocket 兼容（最后一步）=====
  // 把所有 include-all 组就地展开为节点名字数组，删除 mihomo 私有字段
  // ================================================
  expandIncludeAllGroups(config);

  return config;
}


// ============================================================
// 入口暴露——同时兼容 Sparkle / Sub-Store / iPhone Shadowrocket
// ============================================================

if (typeof globalThis !== "undefined") {
  globalThis.main = main;
}
