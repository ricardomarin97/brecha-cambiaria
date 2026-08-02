// Genera www/ a partir del frontend web (static/index.html).
// Un solo HTML sirve a la web y a la app: aqui se inyecta la URL
// absoluta de la API y el script de push nativo.
const fs = require('fs');
const path = require('path');

// OJO: con www — el dominio raiz responde 301 sin cabeceras CORS
// y el WebView de la app bloquea esa redireccion
const API_URL = 'https://www.brecha-cambiaria.com';

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'static');
const DEST = path.join(__dirname, 'www');

fs.mkdirSync(DEST, { recursive: true });

let html = fs.readFileSync(path.join(SRC, 'index.html'), 'utf8');

// La app consume la API de produccion (no hay servidor local)
html = html.replace(
  '</head>',
  `  <script>window.API_BASE = "${API_URL}";</script>\n  </head>`
);

// Registro de notificaciones push (solo actua dentro del shell nativo)
html = html.replace('</body>', '    <script src="native.js"></script>\n  </body>');

fs.writeFileSync(path.join(DEST, 'index.html'), html);
fs.copyFileSync(path.join(SRC, 'favicon.png'), path.join(DEST, 'favicon.png'));
fs.copyFileSync(path.join(__dirname, 'native-src', 'native.js'), path.join(DEST, 'native.js'));

console.log('www/ sincronizado desde static/ (API: ' + API_URL + ')');
