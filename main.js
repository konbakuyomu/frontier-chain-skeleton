/**
 * frontier-chain-skeleton
 *
 * 通用 mihomo 全 config 覆写脚本，作为 GitHub 公开仓库统一发布，供 Sparkle (Win/Mac) +
 * Sub-Store (Linux/VPS) 双端通过 jsdelivr CDN 共享引用。
 *
 * 入口约定：
 *   - 导出 globalThis.main = function main(config)，接收 mihomo 完整配置对象，返回修改后的配置
 *   - Sparkle override 兼容这种签名（旧版用 operator(proxies) 包装，新版直接吃 main(config)）
 *   - Sub-Store mihomoProfile 文件类型通过 operator(input) 消费上一个 operator 的 $content
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


// ============================================================
// 主入口
// ============================================================

function main(config) {
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
    // 注：组挂了大图标（icon 字段），组名故意不带 emoji 前缀
    // 详见 .trellis/spec/network/proxy-group-flexibility.md §5 图标 + 命名规则
    const paypalGroupName = "PayPal";
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
        url: "https://cp.cloudflare.com/generate_204",
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
      if (!globalGroup.proxies.includes(paypalGroupName)) {
        globalGroup.proxies.splice(insertGlobalAt, 0, paypalGroupName);
        insertGlobalAt += 1;
      }
    }

    // 3) 注册用户级 rule-providers（与 AI 同模式：mihomo 原生 .mrs，每日刷新）
    // URL 中的 `@` 必须 percent-encode，否则 mihomo HTTP provider 解析失败
    const userProviders = {
      "paypal-meta": {
        type: "http", behavior: "domain", format: "mrs",
        url: "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/paypal.mrs",
        path: "./ruleset/paypal-meta.mrs", interval: 86400,
        proxy: selectGroup,
      },
      "paypal-cn-meta": {
        type: "http", behavior: "domain", format: "mrs",
        url: "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/paypal%40cn.mrs",
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

    // 5) 业务组镜像家宽选择层（spec §6.9）
    //    powerfullz 自动生成的业务组（AI服务/苹果服务/谷歌服务/Netflix/...）proxies 默认只含
    //    18 国 + 选择代理 + 低倍率 + 手动选择 + 直连共 21 项，**不含**我们的 🏡 *家宽 组。
    //    这里把全部 select 类型的非工具组遍历一遍，只追加稳定的 🏡 家宽选择。
    //    区域家宽组留在 🏡 家宽选择 内部，避免每个业务组都摊开 9 个家宽选项。
    //    push 到末尾不改 default，原 powerfullz 选首项的默认行为保留。
    //    具体家宽节点只出现在 🏡 家宽选择 内部，由 include-all + filter 动态吸纳。
    const TOOL_GROUPS_EXCLUDE = new Set([
      "GLOBAL",
      "选择代理", "手动选择", "自动选择", "故障转移",
      "落地节点", "低倍率节点", "静态资源", "前置代理",
      "直连", "DIRECT", "REJECT",
      RESIDENTIAL_SELECTOR_NAME,
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
    for (const g of businessGroups) {
      g.proxies = withoutRegionResidentialGroups(g.proxies);
      if (!g.proxies.includes(RESIDENTIAL_SELECTOR_NAME)) {
        g.proxies.push(RESIDENTIAL_SELECTOR_NAME);
        mirroredCount++;
      }
    }
    logInfo(`业务组镜像家宽选择层完成：${businessGroups.length} 组追加，共 ${mirroredCount} 处组插入`);
  }

  // ================================================
  // ===== Shadowrocket 兼容（最后一步）=====
  // 把所有 include-all 组就地展开为节点名字数组，删除 mihomo 私有字段
  // ================================================
  expandIncludeAllGroups(config);

  return config;
}


// ============================================================
// 入口暴露——兼容 Sparkle / Sub-Store mihomoProfile
// ============================================================

async function operator(input = [], targetPlatform, context) {
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

    input.$content = ProxyUtils.yaml.safeDump(await main(config));
    return input;
  }

  if (
    input &&
    typeof input === "object" &&
    !Array.isArray(input) &&
    (Array.isArray(input.proxies) || Array.isArray(input["proxy-groups"]) || Array.isArray(input.rules))
  ) {
    return main(input);
  }

  return input;
}

if (typeof globalThis !== "undefined") {
  globalThis.main = main;
  globalThis.__frontierSkeletonMain = main;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { main, operator };
}
