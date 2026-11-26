// Applies the cached admin brand and theme before the first paint (no flash of the wrong brand).
// A classic script file, not inline: the CSP allows only 'self' scripts. The SPA asks the server
// (GET /api/brand) and corrects the cache; keys and values must match src/brand.ts.
(function () {
  var html = document.documentElement
  var brand = 'iler'
  var theme = 'light'
  try {
    if (window.localStorage.getItem('iler.brand') === 'nox') brand = 'nox'
    if (window.localStorage.getItem('iler.theme') === 'dark') theme = 'dark'
  } catch (e) {
    /* storage blocked: defaults */
  }
  html.setAttribute('data-brand', brand)
  if (brand === 'nox') {
    html.setAttribute('data-theme', theme)
    document.title = 'Nox — админка'
    var icon = document.querySelector('link[rel="icon"]')
    if (icon) icon.setAttribute('href', '/favicon-nox.svg')
  }
})()
