# JAVITEN PI3 · Desk Screen

Una pantalla de escritorio que reutiliza una Raspberry Pi para reproducir videos cortos en bucle aleatorio. Desde un navegador en la misma red se administra la biblioteca, se programa cuándo se ve la imagen y se activa AirPlay a demanda.

**Versión del repositorio · v1.0.0**

Primera publicación del código, con asistente inicial y panel bilingüe.

## Hardware

- Raspberry Pi 3 Model B Rev 1.2, identificada en el equipo por el sistema.
- LCD SPI de 3,5 pulgadas, 480 × 320, con controlador `fb_ili9486` y framebuffer `/dev/fb1`.
- microSD para el sistema y la biblioteca MP4. Conexión Wi-Fi para acceder al panel en el puerto 8080.

## Qué hace

- Ofrece inglés por defecto y permite cambiar a español en el asistente, el ingreso y el menú de ajustes del panel.
- El asistente pide un nombre, un usuario web y una contraseña. El nombre se puede cambiar después en el panel y se usa en AirPlay y en la pantalla de arranque. El hostname del sistema se administra por separado.

- Reproduce MP4 mediante FFmpeg y mezcla la cola evitando repetir el último video al volver a barajar. El panel permite reproducir, repetir, poner en cola o saltar videos.
- Sube archivos al elegirlos, muestra el progreso y comprueba el nombre y el espacio disponible. La biblioteca incluye miniaturas, descarga y papelera.
- Enciende o pone en negro la pantalla a demanda o según días y horarios. El apagado detiene la reproducción y cierra AirPlay. La Pi queda accesible por Wi-Fi y la retroiluminación permanece encendida con este hardware.
- Activa y desactiva UxPlay desde el panel. El PIN aparece cuando AirPlay está activo.
- Muestra el espacio libre de la microSD y organiza la carga, los horarios y la biblioteca en secciones plegables.

El botón de engranaje abre los ajustes de idioma, nombre, zona horaria y vista inicial de la biblioteca. También muestra la dirección del panel y el nombre de red.

## Cómo está hecho

`app/player.py` controla la reproducción, la salida al framebuffer y UxPlay. `app/display.py` guarda y aplica los horarios en la zona horaria elegida para el panel, con la de la Raspberry como valor predeterminado. `app/panel.py` sirve el dashboard y se comunica con el reproductor por un socket Unix. Los dos procesos corren como servicios de systemd.

## Preparar otra Raspberry

Las unidades de ejemplo suponen una cuenta local `admin`, un LCD que ya funciona en `/dev/fb1` y las rutas originales del proyecto. Configurá el controlador de tu LCD antes de iniciar los servicios. Se necesita Python 3.9 o posterior, FFmpeg con salida fbdev, DejaVu Sans y, para AirPlay, UxPlay con GStreamer y fbdevsink.

1. Creá `/home/admin/desk-screen/videos` con acceso de escritura para `admin` y copiá los MP4 que quieras reproducir.
2. Instalá los archivos de `app/` como archivos de root en `/opt/desk-screen-panel/` y las dos unidades de `systemd/` en `/etc/systemd/system/`.
3. Creá `/etc/desk-screen-panel.conf` con `LAN_CIDR=192.168.1.0/24` ajustado a tu red. Opcionalmente agregá `LOCAL_NAME=rpi3.local` si el nombre resuelve en tu red.
4. Generá el código de instalación en la Pi con `sudo sh -c 'umask 077; openssl rand -hex 16 > /etc/desk-screen-setup.code'`.
5. Ejecutá `sudo systemctl daemon-reload` y `sudo systemctl enable --now desk-screen.service desk-screen-panel.service`. Abrí la IP local de la Pi en el puerto 8080.
6. Consultá el código con `sudo cat /etc/desk-screen-setup.code`. Ingresalo en el asistente y elegí el idioma, el nombre, el usuario web y una contraseña de al menos 12 caracteres. Después de guardar, el código deja de habilitar el asistente. Conservá el archivo para que systemd pueda cargar la unidad de ejemplo.

El panel usa HTTP en la LAN. Completá el asistente desde una red de confianza. Las unidades son ejemplos de instalación y las rutas deben ajustarse si usás otra cuenta o pantalla. El usuario del asistente es una cuenta web, separada de la cuenta de Linux.

Para conservar un ingreso anterior al actualizar manualmente una instalación existente, el panel sigue admitiendo la configuración `TOKEN_FILE` con el usuario web `admin`. Aplicá los archivos y unidades de manera coordinada.
