# Show de inauguración CIIS: instrucciones

Antes de empezar, pon las IP reales en `NAO1_IP` / `NAO2_IP` al inicio de `show_ciis.py`, o pásalas con `--ip-nao1` / `--ip-nao2`.

## 1. Ejecutar dentro del NAO 1 por SSH (opción principal)

Windows ya trae `scp` y `ssh`. Desde PowerShell, en la carpeta del script (la contraseña de fábrica suele ser `nao`):

```powershell
scp show_ciis.py nao@IP_NAO_1:/home/nao/
ssh nao@IP_NAO_1
```

Ya dentro del robot:

```sh
python /home/nao/show_ciis.py --ip-nao1 127.0.0.1 --ip-nao2 IP_NAO_2 --identificar   # confirmar quién es quién
python /home/nao/show_ciis.py --ip-nao1 127.0.0.1 --ip-nao2 IP_NAO_2                 # show
```

El script prepara todo y muestra `LISTO: presiona ENTER para empezar`. Si las tildes se ven mal en la consola, ejecuta antes `chcp 65001` en Windows. Esto solo afecta a la pantalla, no a lo que dicen los robots.

## 2. Ejecutar desde una PC con el SDK

Esta PC ya tiene pynaoqi 2.8.6.23 para Python 2.7 (en `PYTHONPATH`):

```powershell
py -2.7 show_ciis.py --ip-nao1 IP_NAO_1 --ip-nao2 IP_NAO_2
```

Con Python 3 hace falta el paquete `qi` para Python 3. Sin él, Python 3 solo sirve para `--simular`.

## 3. Lanzar desde un bloque Python de Choregraphe (conectado al NAO 1)

1. Copia `show_ciis.py` a `/home/nao/` con `scp`, como en el punto 1.
2. Crea un bloque Python (Box > New box > Python) y pega esto:

```python
import sys

class MyClass(GeneratedClass):
    def __init__(self):
        GeneratedClass.__init__(self)
        self.show = None

    def onLoad(self):
        if "/home/nao" not in sys.path:
            sys.path.append("/home/nao")

    def onUnload(self):
        if self.show is not None:
            self.show.detener()          # callar y dejar de pie

    def onInput_onStart(self):
        import show_ciis
        opciones = show_ciis.parsear_argumentos([
            "--ip-nao1", "127.0.0.1",
            "--ip-nao2", "IP_NAO_2",
            "--senal", "cabeza",         # en Choregraphe no hay ENTER
            # "--offset-nao1", "20",
        ])
        self.show = show_ciis.ShowCIIS(opciones, log=self.logger.info)
        self.show.correr()
        self.show = None
        self.onStopped()

    def onInput_onStop(self):
        self.onUnload()
        self.onStopped()
```

- Con `--senal cabeza`, el show empieza al tocar el sensor delantero de la cabeza del NAO 1. Con `--senal ninguna`, empieza al terminar la preparación.
- El registro aparece en el panel *Log viewer* de Choregraphe.
- Desde Choregraphe no uses `--vida-autonoma desactivar`: cambiar el estado de Autonomous Life puede detener el propio behavior. El modo por defecto (`ajustar`) no cambia ese estado.

## 4. Afinar los offsets en el ensayo

1. Ensaya solo el final: `--desde 10` (cuenta regresiva + frase AMBOS) o `--desde 11` (solo la frase).
2. Graba en video desde la posición del público y escucha el inicio de "¡Inauguramos…".
3. Si se oye antes el NAO 1, súbele el retraso: `--offset-nao1 20`. Si se oye antes el NAO 2, usa `--offset-nao2 20`. Ajusta de 10 a 20 ms por prueba. Se aceptan valores negativos.
4. La consola muestra la latencia medida, cuánto se retrasó cada robot y el *desfase al terminar*. Úsalo como referencia: la palabra final la tiene el oído.
5. Los valores que funcionen se pueden dejar fijos: en el bloque de Choregraphe o como `default` de `--offset-nao1` / `--offset-nao2`.

Otros ajustes útiles:
- `--pausa` (0.3 s entre líneas) y `--pausa-ambos` (0.4 s tras el "tres").
- `--velocidad`, `--volumen`, `--sin-gestos`, `--vida-autonoma no-tocar`.
- `--animacion-saludo` para usar otra animación en el saludo.
- En "¡Inauguramos…!" no hay animación: los robots levantan los brazos con los motores, sin ningún sonido, y los bajan al terminar la frase. La postura se ajusta en `BRAZOS_ARRIBA` y los tiempos en `TIEMPO_SUBIR_BRAZOS` / `TIEMPO_BAJAR_BRAZOS`, al inicio del script.

## Baile final

Después de la frase al unísono, el NAO 1 dice su frase de anuncio (línea 12). Luego **los dos bailan juntos**: el NAO 1 hace `dancemoves-de295e` y el NAO 2 hace `naoacompaamiento-1bb1c2`. Los dos comportamientos arrancan en el mismo instante, con la misma compensación de latencia y los mismos offsets (`--offset-nao1` / `--offset-nao2`) que la frase al unísono. En la preparación se precargan, para que ninguno se atrase por estar cargando.

El baile termina cuando terminan los dos. Luego se corta cualquier música que siga sonando y ambos vuelven a Stand. La consola muestra cuándo terminó cada uno y el desfase entre ellos.

- En la preparación (paso 5), revisa qué comportamientos encontró el script en cada robot. Se buscan por nombre: basta con parte de él.
- Si el NAO 1 no tiene su baile, se omiten el anuncio y el baile, y el show termina en la línea 11. Si el que falta es el del NAO 2, el NAO 2 se queda de pie.
- Opciones: `--sin-baile`, `--baile-nao1 <ruta o nombre>` y `--baile-nao2 <ruta o nombre>`.
- `--duracion-max-baile 15` corta los dos bailes y su música a los 15 s. El valor por defecto es `0`, sin corte.
- La protección de caídas de NAOqi (FallManager) queda activada en ambos.
- Ensayar solo el final: `--desde 12`.

## Prueba sin robots

```powershell
py -2.7 show_ciis.py --simular
```
