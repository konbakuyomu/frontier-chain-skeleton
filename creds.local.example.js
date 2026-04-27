/**
 * 凭据模板 —— 把这个文件复制为 creds.local.js（不入 git），填入真凭据
 *
 * 用法见 README.md "Sparkle 端使用方式"节。
 */

(function injectCreds() {
  if (typeof globalThis === "undefined") return;

  const SCRAPEGW_RAW =
    "<host>:<port>:<user>:<pass>";
  const [scrapegwHost, scrapegwPort, scrapegwUser, scrapegwPass] = SCRAPEGW_RAW.split(":");

  globalThis.__creds = globalThis.__creds || {};
  Object.assign(globalThis.__creds, {
    scrapegw_host: scrapegwHost,
    scrapegw_port: parseInt(scrapegwPort, 10),
    scrapegw_user: scrapegwUser,
    scrapegw_pass: scrapegwPass,
    frontier_server: "<frontier-host>",
    frontier_port: 0,
    frontier_password: "<frontier-pass>",
    frontier_cipher: "chacha20-ietf-poly1305",
    vps_server: "<vps-ip>",
    vps_port: 0,
    vps_password: "<vps-pass>",
    vps_cipher: "chacha20-ietf-poly1305",
  });
})();
