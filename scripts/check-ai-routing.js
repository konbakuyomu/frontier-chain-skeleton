"use strict";

const assert = require("node:assert/strict");
const { main } = require("../main.js");

function firstTarget(rules, host) {
  for (const rule of rules) {
    const parts = String(rule).split(",").map(part => part.trim());
    const [kind, value] = parts;
    const target = parts[parts.length - 1] === "no-resolve"
      ? parts[parts.length - 2]
      : parts[parts.length - 1];
    if (kind === "DOMAIN" && host === value) return target;
    if (kind === "DOMAIN-SUFFIX" && (host === value || host.endsWith(`.${value}`))) return target;
    if (kind === "MATCH") return target;
  }
  return "";
}

const config = {
  proxies: [{ name: "US 家宽", type: "ss" }],
  "proxy-groups": [
    { name: "选择代理", type: "select", proxies: ["DIRECT"] },
    { name: "手动选择", type: "select", proxies: ["US 家宽", "DIRECT"] },
    { name: "谷歌服务", type: "select", proxies: ["选择代理", "DIRECT"] },
    { name: "AI服务", type: "select", proxies: ["选择代理", "DIRECT"] },
    { name: "GLOBAL", type: "select", proxies: ["选择代理", "AI服务", "DIRECT"] },
  ],
  "rule-providers": {
    "ai-dustin": { type: "http", url: "https://example.invalid/legacy-ai.mrs" },
    untouched: { type: "http", url: "https://example.invalid/untouched.list" },
  },
  rules: [
    "RULE-SET,ai-dustin,AI服务",
    "DOMAIN-SUFFIX,sentry.io,AI服务",
    "DOMAIN-SUFFIX,x.com,AI服务",
    "DOMAIN-SUFFIX,x.com,选择代理",
    "MATCH,选择代理",
    "DOMAIN-SUFFIX,openai.com,AI服务",
  ],
};

const output = main(config);
const groups = new Map(output["proxy-groups"].map(group => [group.name, group]));
assert.equal(groups.get("AI服务").proxies[0], "🏡 家宽选择");
assert.equal(groups.get("AI服务").proxies.filter(name => name === "🏡 家宽选择").length, 1);
assert.equal(groups.get("PayPal").proxies[0], "🏡 家宽选择");
assert.equal(groups.get("PayPal").proxies.filter(name => name === "🏡 家宽选择").length, 1);
assert.equal(groups.get("谷歌服务").proxies[0], "选择代理");
assert.equal(groups.get("自有域名").proxies[0], "DIRECT");

assert.equal(output["rule-providers"]["ai-dustin"], undefined);
assert.ok(output["rule-providers"]["ai-openai"]);
assert.ok(output["rule-providers"]["ai-anthropic"]);
assert.ok(output["rule-providers"]["ai-xai"]);
assert.ok(output["rule-providers"]["ai-community-supplement"]);
assert.ok(output["rule-providers"].untouched);

assert.equal(firstTarget(output.rules, "api.openai.com"), "AI服务");
assert.equal(firstTarget(output.rules, "api.anthropic.com"), "AI服务");
assert.equal(firstTarget(output.rules, "console.anthropic.com"), "AI服务");
assert.equal(firstTarget(output.rules, "assets-proxy.anthropic.com"), "AI服务");
assert.equal(firstTarget(output.rules, "claude.googleapis.com"), "AI服务");
assert.equal(firstTarget(output.rules, "api.x.ai"), "AI服务");
assert.equal(firstTarget(output.rules, "grok.x.com"), "AI服务");
assert.equal(firstTarget(output.rules, "x.com"), "选择代理");
assert.equal(firstTarget(output.rules, "github.com"), "选择代理");
assert.equal(firstTarget(output.rules, "registry.npmjs.org"), "选择代理");
assert.equal(firstTarget(output.rules, "example.cloudflare.com"), "选择代理");
assert.equal(firstTarget(output.rules, "example.googleapis.com"), "选择代理");
assert.equal(firstTarget(output.rules, "telemetry.statsigapi.net"), "选择代理");
assert.equal(firstTarget(output.rules, "cpa.konbakuyomu.us"), "自有域名");
assert.equal(output.rules.some(rule => rule === "DOMAIN-SUFFIX,sentry.io,AI服务"), false);
assert.equal(output.rules.some(rule => rule === "DOMAIN-SUFFIX,x.com,AI服务"), false);
assert.equal(output.rules.filter(rule => rule === "DOMAIN-SUFFIX,openai.com,AI服务").length, 1);
assert.ok(
  output.rules.indexOf("DOMAIN-SUFFIX,openai.com,AI服务")
    < output.rules.indexOf("RULE-SET,ai-openai,AI服务")
);

const extraConfig = {
  proxies: [{ name: "US 家宽", type: "ss" }],
  "proxy-groups": [
    { name: "选择代理", type: "select", proxies: ["DIRECT"] },
    { name: "手动选择", type: "select", proxies: ["US 家宽", "DIRECT"] },
    { name: "谷歌服务", type: "select", proxies: ["选择代理", "DIRECT"] },
    { name: "AI服务", type: "select", proxies: ["选择代理", "DIRECT"] },
    { name: "GLOBAL", type: "select", proxies: ["选择代理", "AI服务", "DIRECT"] },
  ],
  "rule-providers": {},
  rules: ["MATCH,选择代理"],
};
globalThis.__frontierExtraAiApiHosts = ["api.example-gateway.example"];
const extraOutput = main(extraConfig);
assert.equal(firstTarget(extraOutput.rules, "api.example-gateway.example"), "AI服务");
delete globalThis.__frontierExtraAiApiHosts;

console.log("OK: AI routing first-match and selector behavior passed");
