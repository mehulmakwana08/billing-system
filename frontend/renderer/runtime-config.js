/*
 Runtime deployment config.
 Set webApiBase to a shared hosted backend URL (with or without /api).
 Keep it empty to use same-origin /api in web mode.
*/
window.BILLING_RUNTIME_CONFIG = Object.assign(
  {
    webApiBase: '',
  },
  window.BILLING_RUNTIME_CONFIG || {}
)
