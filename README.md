# JAVITEN PI3 · Desk Screen

Una pantalla de escritorio que reutiliza una Raspberry Pi para reproducir videos cortos en bucle aleatorio. Desde un navegador en la misma red se administra la biblioteca, se programa cuándo se ve la imagen y se activa AirPlay a demanda.

**Versión del repositorio · v1.0.0**

Primera publicación del código. Reúne la versión instalada y los cambios del panel.

## Hardware

- Raspberry Pi 3 Model B Rev 1.2, identificada en el equipo por el sistema.
- LCD SPI de 3,5 pulgadas, 480 × 320, con controlador `fb_ili9486` y framebuffer `/dev/fb1`.
- microSD para el sistema y la biblioteca MP4. Conexión Wi-Fi para acceder al panel en el puerto 8080.

## Qué hace

- Reproduce MP4 mediante FFmpeg y mezcla la cola evitando repetir el último video al volver a barajar. El panel permite reproducir, repetir, poner en cola o saltar videos.
- Sube archivos al elegirlos, muestra el progreso y comprueba el nombre y el espacio disponible. La biblioteca incluye miniaturas, descarga y papelera.
- Enciende o pone en negro la pantalla a demanda o según días y horarios. El apagado detiene la reproducción y cierra AirPlay. La Pi queda accesible por Wi-Fi y la retroiluminación permanece encendida con este hardware.
- Activa y desactiva UxPlay desde el panel. El PIN aparece cuando AirPlay está activo.
- Muestra el espacio libre de la microSD y organiza la carga, los horarios y la biblioteca en secciones plegables.

## Cómo está hecho

`app/player.py` controla la reproducción, la salida al framebuffer y UxPlay. `app/display.py` guarda y aplica los horarios en la zona horaria del sistema. `app/panel.py` sirve el dashboard y se comunica con el reproductor por un socket Unix. Los dos procesos corren como servicios de systemd.

## Preparar otra Raspberry

Las unidades de ejemplo suponen una cuenta local `admin`, un LCD que ya funciona en `/dev/fb1` y las rutas originales del proyecto. Configurá el controlador de tu LCD antes de iniciar los servicios. Se necesita Python 3.9 o posterior, FFmpeg con salida fbdev, DejaVu Sans y, para AirPlay, UxPlay con GStreamer y fbdevsink.

1. Creá `/home/admin/desk-screen/videos` con acceso de escritura para `admin` y copiá los MP4 que quieras reproducir.
2. Instalá los archivos de `app/` como archivos de root en `/opt/desk-screen-panel/` y las dos unidades de `systemd/` en `/etc/systemd/system/`.
3. Creá `/etc/desk-screen-panel.conf` con `LAN_CIDR=192.168.1.0/24` ajustado a tu red. Opcionalmente agregá `LOCAL_NAME=rpi3.local` si el nombre resuelve en tu red.
4. Guardá una contraseña única en `/etc/desk-screen-panel.token` con propietario root y modo 0600. El usuario web es `admin`.
5. Ejecutá `sudo systemctl daemon-reload` y `sudo systemctl enable --now desk-screen.service desk-screen-panel.service`. Abrí la IP local de la Pi en el puerto 8080.

El panel usa HTTP en la LAN. Mantenelo en una red de confianza. Las unidades son ejemplos de instalación y las rutas deben ajustarse si usás otra cuenta o pantalla.
