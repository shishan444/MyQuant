// MyQuant 前端运行时配置（fix-audit-disclosure-security S-02）。
//
// 部署时由运维覆盖本文件注入实值（构建产物零内联密钥——Vite 构建期
// 编译 import.meta.env 的旧形制曾把 API key 明文内联进 dist 静态产物，
// 产物离机即泄）；本地开发保持空串（未设不带 Authorization，兼容放行态）。
window.__MYQUANT_RUNTIME_CONFIG__ = {
  MYQUANT_API_KEY: "",
};
