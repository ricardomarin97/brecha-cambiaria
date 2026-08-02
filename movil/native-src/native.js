// Registro de notificaciones push. Solo corre dentro del shell
// nativo de Capacitor; en el navegador no hace nada.
(function () {
  const cap = window.Capacitor;
  if (!cap || !cap.isNativePlatform || !cap.isNativePlatform()) return;

  const Push = cap.Plugins && cap.Plugins.PushNotifications;
  if (!Push) {
    console.warn('Plugin PushNotifications no disponible');
    return;
  }

  const API = window.API_BASE || '';

  Push.addListener('registration', (token) => {
    fetch(API + '/api/devices', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token.value, platform: cap.getPlatform() })
    }).catch((e) => console.error('Error enviando token al servidor:', e));
  });

  Push.addListener('registrationError', (err) => {
    console.error('Error de registro push:', JSON.stringify(err));
  });

  async function registrarPush() {
    try {
      let permiso = await Push.checkPermissions();
      if (permiso.receive === 'prompt') {
        permiso = await Push.requestPermissions();
      }
      if (permiso.receive !== 'granted') return;
      await Push.register();
    } catch (e) {
      console.error('Error registrando push:', e);
    }
  }

  registrarPush();
})();
