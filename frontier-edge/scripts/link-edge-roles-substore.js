#!/usr/bin/env node
/*
 * Link the generated edge role local sub into client-facing Sub-Store
 * collections. Run inside the Sub-Store container or on a host that can access
 * sub-store.json. Prints only collection names and counts.
 */

const fs = require("fs");

const dataPath = process.argv[2] || "/opt/app/data/sub-store.json";
const roleSubName = process.env.EDGE_ROLE_SUB || "edge-us-roles";
const hy2RoleSubName = process.env.EDGE_HY2_ROLE_SUB || "edge-us-hy2-roles";
const collectionNames = (process.env.EDGE_ROLE_COLLECTIONS || "merged-airports,ios-airports-uri")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);
const hy2CollectionNames = (process.env.EDGE_HY2_ROLE_COLLECTIONS || "merged-airports,ios-evoxt-hy2-shadowrocket")
  .split(",")
  .map((name) => name.trim())
  .filter(Boolean);

function findNamed(items, name) {
  return (items || []).find((item) => item && item.name === name);
}

const data = JSON.parse(fs.readFileSync(dataPath, "utf8"));
if (!findNamed(data.subs, roleSubName)) {
  console.error(`ERROR: missing sub: ${roleSubName}`);
  process.exit(1);
}
const hy2Sub = findNamed(data.subs, hy2RoleSubName);
const shouldLinkHy2 = process.env.EDGE_ENABLE_HY2 === "1" || Boolean(hy2Sub);
if (shouldLinkHy2 && !hy2Sub) {
  console.error(`ERROR: missing sub: ${hy2RoleSubName}`);
  process.exit(1);
}

const changed = [];
const touchedCollections = new Set();
function linkSubToCollections(subName, names) {
  for (const name of names) {
    const collection = findNamed(data.collections, name);
    if (!collection) {
      console.error(`ERROR: missing collection: ${name}`);
      process.exit(1);
    }
    collection.subscriptions = Array.isArray(collection.subscriptions) ? collection.subscriptions : [];
    if (!collection.subscriptions.includes(subName)) {
      collection.subscriptions.push(subName);
      changed.push(`collection-linked:${name}:${subName}`);
    }
    touchedCollections.add(name);
  }
}

linkSubToCollections(roleSubName, collectionNames);
if (shouldLinkHy2) {
  linkSubToCollections(hy2RoleSubName, hy2CollectionNames);
}

const tmpPath = `${dataPath}.tmp-frontier-edge-link-${process.pid}`;
fs.writeFileSync(tmpPath, `${JSON.stringify(data, null, 2)}\n`);
fs.renameSync(tmpPath, dataPath);

console.log(JSON.stringify({
  roleSub: roleSubName,
  hy2RoleSub: shouldLinkHy2 ? hy2RoleSubName : "",
  changed,
  collections: Array.from(touchedCollections).map((name) => {
    const collection = findNamed(data.collections, name);
    return { name, subscriptions: collection.subscriptions.length };
  }),
}, null, 2));
