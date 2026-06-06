#!/usr/bin/env node
/*
 * Ensure the minimal Sub-Store runtime objects required by frontier-edge.
 *
 * This script runs inside the Sub-Store container or anywhere that can read the
 * data file. It reads the upstream URI from stdin, writes only sub-store.json,
 * and prints object names/counts without echoing secrets.
 */

const fs = require("fs");
const path = require("path");

const dataPath = process.argv[2] || "/opt/app/data/sub-store.json";
const upstreamName = process.env.EDGE_UPSTREAM_SUB || "edge-us-att";
const upstreamDisplay = process.env.EDGE_UPSTREAM_DISPLAY || "20-原料-家宽-美国-AT&T";
const collectionName = process.env.EDGE_UPSTREAM_COLLECTION || "edge-us-upstreams";
const collectionDisplay = process.env.EDGE_UPSTREAM_COLLECTION_DISPLAY || "20-原料-家宽-美国Edge上游";
const roleSubName = process.env.EDGE_ROLE_SUB || "edge-us-roles";
const roleSubDisplay = process.env.EDGE_ROLE_SUB_DISPLAY || "40-稳定角色-美国Edge家宽";

function readStdin() {
  return fs.readFileSync(0, "utf8").trim();
}

function findNamed(items, name) {
  return (items || []).find((item) => item && item.name === name);
}

function quickSettingOperator() {
  return {
    type: "Quick Setting Operator",
    args: {
      useless: "DISABLED",
      udp: "DEFAULT",
      scert: "DEFAULT",
      tfo: "DEFAULT",
      "vmess aead": "DEFAULT",
    },
  };
}

function ensureUpstreamSub(data, name, displayName, upstreamUri) {
  const subs = data.subs || (data.subs = []);
  let sub = findNamed(subs, name);
  const isRemote = /^https?:\/\//i.test(upstreamUri);
  const changed = [];
  if (!sub) {
    sub = {
      name,
      "display-name": displayName,
      displayName,
      source: isRemote ? "remote" : "local",
      url: isRemote ? upstreamUri : "",
      content: isRemote ? "" : upstreamUri,
      form: "",
      ua: "",
      mergeSources: "",
      passThroughUA: false,
      ignoreFailedRemoteSub: true,
      isIconColor: true,
      icon: "",
      tag: [],
      subscriptionTags: [],
      process: [quickSettingOperator()],
    };
    subs.push(sub);
    changed.push(`sub-created:${name}`);
  } else {
    const nextSource = isRemote ? "remote" : "local";
    if (sub.source !== nextSource) {
      sub.source = nextSource;
      changed.push(`sub-source-updated:${name}`);
    }
    const nextUrl = isRemote ? upstreamUri : "";
    const nextContent = isRemote ? "" : upstreamUri;
    if (sub.url !== nextUrl) {
      sub.url = nextUrl;
      changed.push(`sub-url-updated:${name}`);
    }
    if (sub.content !== nextContent) {
      sub.content = nextContent;
      changed.push(`sub-content-updated:${name}`);
    }
    for (const key of ["display-name", "displayName"]) {
      if (sub[key] !== displayName) {
        sub[key] = displayName;
        changed.push(`sub-display-updated:${name}`);
      }
    }
    sub.source = nextSource;
    sub.form = sub.form || "";
    sub.ua = sub.ua || "";
    sub.tag = Array.isArray(sub.tag) ? sub.tag : [];
    sub.subscriptionTags = Array.isArray(sub.subscriptionTags) ? sub.subscriptionTags : [];
    sub.process = Array.isArray(sub.process) ? sub.process : [quickSettingOperator()];
    if (isRemote && sub.ignoreFailedRemoteSub !== true) {
      sub.ignoreFailedRemoteSub = true;
      changed.push(`sub-ignore-failed-enabled:${name}`);
    }
  }
  return changed;
}

function ensureLocalSub(data, name, displayName) {
  const subs = data.subs || (data.subs = []);
  let sub = findNamed(subs, name);
  const changed = [];
  if (!sub) {
    sub = {
      name,
      "display-name": displayName,
      displayName,
      source: "local",
      url: "",
      content: "",
      form: "",
      ua: "",
      mergeSources: "",
      passThroughUA: false,
      ignoreFailedRemoteSub: false,
      isIconColor: true,
      icon: "",
      tag: [],
      subscriptionTags: [],
      process: [quickSettingOperator()],
    };
    subs.push(sub);
    changed.push(`sub-created:${name}`);
  } else {
    for (const key of ["display-name", "displayName"]) {
      if (sub[key] !== displayName) {
        sub[key] = displayName;
        changed.push(`sub-display-updated:${name}`);
      }
    }
    sub.source = "local";
    sub.url = "";
    sub.content = sub.content || "";
    sub.process = Array.isArray(sub.process) ? sub.process : [quickSettingOperator()];
  }
  return changed;
}

function ensureCollection(data, name, displayName, subscriptions) {
  const collections = data.collections || (data.collections = []);
  let col = findNamed(collections, name);
  const changed = [];
  if (!col) {
    col = {
      name,
      "display-name": displayName,
      displayName,
      firstSubFlow: true,
      form: "",
      icon: "",
      ignoreFailedRemoteSub: true,
      isIconColor: true,
      mergeSources: "",
      passThroughUA: false,
      process: [quickSettingOperator()],
      remark: "",
      subscriptionTags: [],
      subscriptions: [],
      tag: [],
    };
    collections.push(col);
    changed.push(`collection-created:${name}`);
  }
  for (const key of ["display-name", "displayName"]) {
    if (col[key] !== displayName) {
      col[key] = displayName;
      changed.push(`collection-display-updated:${name}`);
    }
  }
  if (col.ignoreFailedRemoteSub !== true) {
    col.ignoreFailedRemoteSub = true;
    changed.push(`collection-ignore-failed-enabled:${name}`);
  }
  col.subscriptions = Array.isArray(col.subscriptions) ? col.subscriptions : [];
  for (const subName of subscriptions) {
    if (!col.subscriptions.includes(subName)) {
      col.subscriptions.push(subName);
      changed.push(`collection-linked:${name}:${subName}`);
    }
  }
  col.process = Array.isArray(col.process) ? col.process : [quickSettingOperator()];
  return changed;
}

const upstreamUrl = readStdin();
if (!upstreamUrl || !/^[a-z][a-z0-9+.-]*:\/\//i.test(upstreamUrl)) {
  console.error("ERROR: expected one upstream URI on stdin");
  process.exit(1);
}

const data = JSON.parse(fs.readFileSync(dataPath, "utf8"));
const changed = [
  ...ensureUpstreamSub(data, upstreamName, upstreamDisplay, upstreamUrl),
  ...ensureLocalSub(data, roleSubName, roleSubDisplay),
  ...ensureCollection(data, collectionName, collectionDisplay, [upstreamName]),
];

const tmpPath = `${dataPath}.tmp-frontier-edge-${process.pid}`;
fs.writeFileSync(tmpPath, `${JSON.stringify(data, null, 2)}\n`);
fs.renameSync(tmpPath, dataPath);

console.log(JSON.stringify({
  dataFile: path.basename(dataPath),
  changed,
  upstreamSub: upstreamName,
  roleSub: roleSubName,
  collection: collectionName,
  subsCount: (data.subs || []).length,
  collectionsCount: (data.collections || []).length,
}, null, 2));
