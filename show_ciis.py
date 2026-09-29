# -*- coding: utf-8 -*-
"""
show_ciis.py - Inauguración del 9.º CIIS con dos NAO V6 (NAOqi 2.8).

Un solo script orquesta los dos robots (una sesión qi por robot):
  - diálogo por turnos: cada línea empieza cuando termina la anterior + pausa;
  - frase final al unísono: ambos arrancan al mismo tiempo, compensando la
    latencia de red medida y los offsets manuales de cada robot.

Compatible con Python 2.7 (el Python del robot) y Python 3.
Sin dependencias externas: solo la librería qi y la librería estándar.

Uso típico (dentro del NAO 1 por SSH):
    python show_ciis.py --ip-nao1 127.0.0.1 --ip-nao2 <IP_NAO_2>

Otros modos:
    --identificar   cada robot dice "Soy NAO uno" / "Soy NAO dos"
    --desde N       empieza desde la línea N (ensayos)
    --simular       sin robots: recorre el flujo e imprime tiempos estimados
    Ctrl+C          detiene el habla y deja ambos robots de pie
"""
from __future__ import print_function, division

import argparse
import datetime
import random
import re
import sys
import threading
import time

try:
    import qi
except Exception:  # Sin SDK (o el SDK de Python 2 visto desde Python 3): solo --simular
    qi = None

PY2 = sys.version_info[0] == 2
if PY2:
    TIPO_TEXTO = unicode  # noqa: F821 (solo existe en Python 2)
    leer_linea = raw_input  # noqa: F821
else:
    TIPO_TEXTO = str
    leer_linea = input


# =============================================================================
# CONFIGURACIÓN (todo se puede cambiar también por argumentos)
# =============================================================================
NAO1_IP = "192.168.137.37"      # Reemplazar por la IP real (o usar --ip-nao1)
NAO2_IP = "192.168.137.192"      # Reemplazar por la IP real (o usar --ip-nao2)
PUERTO_NAOQI = 9561

IDIOMA = "Spanish"
VELOCIDAD = 100             # Velocidad del habla en % (etiqueta \rspd=N\)
VOLUMEN = 70                # Volumen general del robot (ALAudioDevice, 0-100)
VOLUMEN_TTS = 1.0           # Volumen del motor TTS (0.0-1.0)

PAUSA_ENTRE_LINEAS = 0.3    # s entre el fin de una línea y el inicio de la siguiente
PAUSA_ANTES_DE_AMBOS = 0.4  # s entre el "tres" y la frase al unísono
PAUSA_CUENTA_MS = 500       # Pausa TTS entre "Uno", "dos" y "tres"
MUESTRAS_LATENCIA = 10      # Llamadas livianas para medir la latencia (mediana)
BATERIA_MINIMA = 30         # % bajo el cual se avisa
DURACION_OBJETIVO = (40.0, 60.0)
TIEMPO_MAXIMO_LINEA = 30.0  # s; si una línea no termina en este tiempo, se sigue

# Animaciones candidatas (se usa la primera que exista en el robot).
# Si ninguna existe, el robot habla sin esa animación: el show no se cae.
ANIMACIONES_SALUDO = [
    "animations/Stand/Gestures/Hey_1",
    "animations/Stand/Gestures/Hey_3",
    "animations/Stand/Gestures/Hey_4",
    "animations/Stand/Gestures/Hey_6",
]
# Brazos arriba en la línea AMBOS: movimiento directo de motores (ALMotion), sin
# animaciones (Winner_2 traía aplausos). Ángulos en radianes, iguales en ambos robots.
ARTICULACIONES_BRAZOS = ["LShoulderPitch", "LShoulderRoll", "LElbowYaw", "LElbowRoll", "LWristYaw", "LHand",
                         "RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw", "RHand"]
BRAZOS_ARRIBA = [-1.35, 0.35, -1.4, -0.1, 0.0, 1.0,   # izquierdo: arriba, abierto, casi estirado, mano abierta
                 -1.35, -0.35, 1.4, 0.1, 0.0, 1.0]    # derecho (espejo)
# Movimiento lento y suave (ALMotion acelera y frena gradualmente en cada movimiento).
TIEMPO_SUBIR_BRAZOS = 3.5   # s en subir: arranca con la voz y llega arriba cerca del final de la frase
TIEMPO_BAJAR_BRAZOS = 3.0   # s en bajar a la postura anterior, al terminar la frase

# Baile final. Se acepta la ruta completa del comportamiento ("paquete/behavior_1")
# o parte del nombre: se busca entre los comportamientos instalados en el robot.
# Los dos bailes empiezan al mismo tiempo (misma compensación que la frase AMBOS).
BAILE_NAO1 = "dancemoves-de295e"
BAILE_NAO2 = "naoacompaamiento-1bb1c2"
DURACION_MAXIMA_BAILE = 0.0          # s; si es > 0, a ese tiempo se cortan bailes y música (0 = sin corte)

NAO1 = "NAO1"
NAO2 = "NAO2"
AMBOS = "AMBOS"
NOMBRES = {NAO1: u"NAO 1", NAO2: u"NAO 2", AMBOS: u"AMBOS"}


# =============================================================================
# GUIÓN (texto aprobado: NO modificar "texto")
# "texto_tts" es opcional: lo que realmente se envía al TTS para ajustar
# pronunciación o pausas sin tocar el texto aprobado.
# =============================================================================
def _linea(hablante, texto, texto_tts=None, animacion=None, requiere=None):
    # requiere="baile": la línea solo se dice si el baile está disponible,
    # y el baile empieza cuando termina esa línea.
    return {"hablante": hablante, "texto": texto, "texto_tts": texto_tts,
            "animacion": animacion, "requiere": requiere}


_PAU = u"\\pau=%d\\" % PAUSA_CUENTA_MS

GUION = [
    _linea(NAO1, u"¡Hola, Alan! ¡Qué gusto verte!", animacion="saludo"),
    _linea(NAO2, u"¡Hola Geoffrey! Cargué mi batería al cien por ciento para no perderme este congreso."),
    _linea(NAO1, u"¡Ya van nueve ediciones! ¿Y cuál es el lema de este año?"),
    _linea(NAO2, u"Ciencia de datos para ecosistemas inteligentes."),
    _linea(NAO1, u"O sea, personas, empresas y máquinas compartiendo datos para tomar mejores decisiones."),
    _linea(NAO2, u"Como tú y yo: un ecosistema inteligente... pequeñito, pero con mucho estilo."),
    _linea(NAO1, u"¡Y son tres días con ponentes internacionales, expertos de la industria y mucha inteligencia artificial!"),
    # Si "Zoom" suena mal en el ensayo, agregar p. ej. texto_tts=u"¡Aquí en el campus y también por Zum!"
    _linea(NAO2, u"¡Aquí en el campus y también por Zum!"),
    _linea(NAO1, u"Bueno, Alan, ya es hora. ¿Lo decimos juntos?"),
    # Cuenta regresiva: misma estructura y misma pausa entre números = ritmo parejo.
    _linea(NAO2, u"A la cuenta de tres. Uno... dos... tres...",
           texto_tts=u"A la cuenta de tres. %s Uno. %s Dos. %s Tres." % (_PAU, _PAU, _PAU)),
    # Sin animación: los brazos se levantan con ALMotion (ver _linea_ambos)
    _linea(AMBOS, u"¡Inauguramos el noveno Congreso Internacional de Ingeniería de Sistemas!"),
    _linea(NAO1, u"Pista, por favor", requiere="baile"),
]


# =============================================================================
# UTILIDADES
# =============================================================================
class ErrorShow(Exception):
    """Error con un mensaje claro para el operador."""


class ShowInterrumpido(Exception):
    """El show se detuvo (Ctrl+C o detener() desde Choregraphe)."""


_ahora_real = getattr(time, "perf_counter", None) or time.time


def _texto(x):
    """Convierte cualquier cosa (incluidos errores de qi) a texto unicode."""
    if isinstance(x, TIPO_TEXTO):
        return x
    if PY2 and isinstance(x, str):
        return x.decode("utf-8", "replace")
    try:
        return TIPO_TEXTO(x)
    except Exception:
        try:
            return str(x).decode("utf-8", "replace") if PY2 else repr(x)
        except Exception:
            return repr(x)


def _para_naoqi(texto):
    """En Python 2, say() debe recibir str codificado en UTF-8 (tildes, ¡, ¿)."""
    if PY2 and isinstance(texto, TIPO_TEXTO):
        return texto.encode("utf-8")
    return texto


def _imprimir(mensaje):
    if PY2:
        # Consola de Windows: su codificación (cp850); SSH/tuberías: UTF-8
        cod = getattr(sys.stdout, "encoding", None) or "utf-8"
        sys.stdout.write(mensaje.encode(cod, "replace") + "\n")
    else:
        try:
            sys.stdout.write(mensaje + "\n")
        except UnicodeEncodeError:
            cod = sys.stdout.encoding or "ascii"
            sys.stdout.write(mensaje.encode(cod, "replace").decode(cod) + "\n")
    sys.stdout.flush()


def _mediana(valores):
    v = sorted(valores)
    n = len(v)
    if n == 0:
        return 0.0
    if n % 2:
        return v[n // 2]
    return (v[n // 2 - 1] + v[n // 2]) / 2.0


_RE_TAG_TTS = re.compile(r"\\(\w+)=(\d+)\\")
_RE_TAG_ANIM = re.compile(r"\^\w+\([^)]*\)")


def estimar_duracion(texto, velocidad=VELOCIDAD):
    """Estimación aproximada (s) de lo que tarda el TTS en decir 'texto'."""
    pausas = sum(int(v) for n, v in _RE_TAG_TTS.findall(texto) if n == "pau") / 1000.0
    limpio = _RE_TAG_ANIM.sub(u" ", _RE_TAG_TTS.sub(u" ", texto))
    palabras = len(re.findall(r"\w+", limpio, re.UNICODE))
    finales = len(re.findall(r"\.\.\.|[.!?:]", limpio))
    comas = limpio.count(u",") + limpio.count(u";")
    palabras_por_seg = 2.6 * velocidad / 100.0
    return 0.3 + palabras / palabras_por_seg + pausas + 0.3 * finales + 0.15 * comas


def texto_para_tts(linea):
    return linea["texto_tts"] or linea["texto"]


def resolver_comportamiento(instalados, patron):
    """Busca 'patron' entre los comportamientos instalados.

    Orden: ruta exacta > paquete ("patron/...") > nombre que contenga el patrón.
    Si hay varias, se prefiere ".../behavior_1". Devuelve None si no hay.
    """
    patron = _texto(patron).strip().strip(u"/")
    visibles = sorted(_texto(b) for b in instalados if not _texto(b).startswith(u"."))

    def preferir(candidatos):
        for b in candidatos:
            if b.endswith(u"/behavior_1"):
                return b
        return candidatos[0] if candidatos else None

    if patron in visibles:
        return patron
    en_paquete = preferir([b for b in visibles if b.startswith(patron + u"/")])
    if en_paquete:
        return en_paquete
    return preferir([b for b in visibles if patron.lower() in b.lower()])


# =============================================================================
# RELOJES: real, o virtual para --simular (no espera de verdad)
# =============================================================================
class RelojReal(object):
    def ahora(self):
        return _ahora_real()

    def dormir(self, segundos):
        if segundos > 0:
            time.sleep(segundos)

    def dormir_hasta(self, t):
        # sleep grueso y "spin" los últimos ms para mayor precisión
        while True:
            resto = t - self.ahora()
            if resto <= 0:
                return
            if resto > 0.004:
                time.sleep(resto - 0.002)

    def hora(self):
        return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


class RelojVirtual(object):
    def __init__(self):
        self._t = 0.0
        self._inicio = datetime.datetime.now()

    def ahora(self):
        return self._t

    def dormir(self, segundos):
        if segundos > 0:
            self._t += segundos

    def dormir_hasta(self, t):
        self._t = max(self._t, t)

    def hora(self):
        h = self._inicio + datetime.timedelta(seconds=self._t)
        return h.strftime("%H:%M:%S.%f")[:-3]


# =============================================================================
# FUTUROS (resultado de una llamada asíncrona a say)
# =============================================================================
class FuturoQi(object):
    def __init__(self, futuro):
        self._f = futuro

    def terminado(self):
        return self._f.isFinished()

    def error(self):
        try:
            if self._f.hasError():
                return _texto(self._f.error())
        except Exception as e:
            return _texto(e)
        return None


class FuturoListo(object):
    """Futuro ya terminado (p. ej. la llamada falló al lanzarse)."""
    def __init__(self, error=None):
        self._error = error

    def terminado(self):
        return True

    def error(self):
        return self._error


class FuturoSimulado(object):
    def __init__(self, reloj, fin):
        self._reloj = reloj
        self._fin = fin

    def terminado(self):
        return self._reloj.ahora() >= self._fin

    def error(self):
        return None

    def cortar(self):
        self._fin = min(self._fin, self._reloj.ahora())


# =============================================================================
# ROBOT REAL
# =============================================================================
class Robot(object):
    def __init__(self, clave, ip, puerto):
        self.clave = clave
        self.nombre = NOMBRES[clave]
        self.ip = ip
        self.puerto = puerto
        self.sesion = None
        self.velocidad = VELOCIDAD
        self._servicios = {}
        self._comportamiento = None  # último comportamiento lanzado (para detenerlo)

    def etiqueta(self):
        return u"%s (%s)" % (self.nombre, _texto(self.ip))

    def conectar(self):
        if qi is None:
            raise ErrorShow(u"No se encontró la librería 'qi'. Ejecuta el script dentro "
                            u"del NAO, en una PC con el SDK de NAOqi, o usa --simular.")
        url = "tcp://%s:%d" % (self.ip, self.puerto)
        try:
            sesion = qi.Session()
            sesion.connect(url)
        except Exception as e:
            raise ErrorShow(u"No se pudo conectar con %s en %s: %s"
                            % (self.nombre, _texto(url), _texto(e)))
        self.sesion = sesion

    def servicio(self, nombre):
        """Devuelve el servicio o None si no existe (cacheado)."""
        if nombre not in self._servicios:
            try:
                self._servicios[nombre] = self.sesion.service(nombre)
            except Exception:
                self._servicios[nombre] = None
        return self._servicios[nombre]

    def _tts(self):
        tts = self.servicio("ALTextToSpeech")
        if tts is None:
            raise ErrorShow(u"%s no tiene ALTextToSpeech disponible." % self.etiqueta())
        return tts

    # --- Información ---------------------------------------------------------
    def version(self):
        try:
            return _texto(self.servicio("ALSystem").systemVersion())
        except Exception:
            return u"desconocida"

    def nombre_robot(self):
        try:
            return _texto(self.servicio("ALSystem").robotName())
        except Exception:
            return u"?"

    def bateria(self):
        try:
            return int(self.servicio("ALBattery").getBatteryCharge())
        except Exception:
            pass
        try:
            valor = self.servicio("ALMemory").getData(
                "Device/SubDeviceList/Battery/Charge/Sensor/Value")
            return int(round(float(valor) * 100))
        except Exception:
            return None

    # --- Voz -----------------------------------------------------------------
    def configurar_voz(self, idioma, velocidad, volumen, volumen_tts):
        tts = self._tts()
        disponibles = [_texto(i) for i in tts.getAvailableLanguages()]
        if _texto(idioma) not in disponibles:
            raise ErrorShow(u"%s no tiene instalado el idioma '%s'. Disponibles: %s"
                            % (self.etiqueta(), _texto(idioma), u", ".join(disponibles)))
        tts.setLanguage(idioma)
        # La velocidad se fija con la etiqueta \rspd=N\ en cada frase; el
        # parámetro global se deja en 100 en ambos para que no se acumule.
        try:
            tts.setParameter("speed", 100)
        except Exception:
            pass
        tts.setVolume(float(volumen_tts))
        self.velocidad = int(velocidad)
        audio = self.servicio("ALAudioDevice")
        vol_general = None
        if audio is not None:
            try:
                audio.setOutputVolume(int(volumen))
                vol_general = audio.getOutputVolume()
            except Exception:
                pass
        # Sonidos de comportamientos al 100 % (versiones anteriores del script los
        # ponían en 0 en la frase AMBOS; así la música del baile nunca queda muda)
        self.volumen_efectos(1.0)
        try:
            voz = _texto(tts.getVoice())
        except Exception:
            voz = u"?"
        return {"idioma": _texto(tts.getLanguage()), "voz": voz,
                "volumen": vol_general, "volumen_tts": tts.getVolume()}

    # --- Vida autónoma y gestos ---------------------------------------------
    def ajustar_vida_autonoma(self, modo):
        """Evita que la cabeza siga caras sin perder los gestos al hablar."""
        msgs = []
        al = self.servicio("ALAutonomousLife")
        if modo == "no-tocar":
            estado = u"?"
            try:
                estado = _texto(al.getState())
            except Exception:
                pass
            return [u"Vida autónoma sin cambios (estado: %s)" % estado]

        if modo == "desactivar" and al is not None:
            try:
                al.setState("disabled")
                msgs.append(u"Autonomous Life -> disabled")
            except Exception as e:
                msgs.append(u"AVISO: no se pudo desactivar Autonomous Life: %s" % _texto(e))

        # Habilidades que mueven la cabeza hacia el público
        for habilidad in ("BasicAwareness", "ListeningMovement"):
            ok = False
            if al is not None:
                try:
                    al.setAutonomousAbilityEnabled(habilidad, False)
                    ok = True
                except Exception:
                    pass
            if not ok and habilidad == "BasicAwareness":
                ba = self.servicio("ALBasicAwareness")
                for metodo in ("setEnabled", "stopAwareness"):
                    try:
                        if metodo == "setEnabled":
                            ba.setEnabled(False)
                        else:
                            ba.stopAwareness()
                        ok = True
                        break
                    except Exception:
                        pass
            msgs.append(u"%s desactivado" % habilidad if ok
                        else u"AVISO: no se pudo desactivar %s" % habilidad)

        # En NAOqi 2.8 los gestos de ALAnimatedSpeech dependen de SpeakingMovement
        if al is not None:
            try:
                al.setAutonomousAbilityEnabled("SpeakingMovement", True)
            except Exception:
                pass
        sm = self.servicio("ALSpeakingMovement")
        if sm is not None:
            try:
                sm.setEnabled(True)
            except Exception:
                pass
        return msgs

    def estado_gestos(self):
        """(ALAnimatedSpeech disponible, SpeakingMovement activo o None)."""
        animado = self.servicio("ALAnimatedSpeech") is not None
        activo = None
        sm = self.servicio("ALSpeakingMovement")
        if sm is not None:
            try:
                activo = bool(sm.isEnabled())
            except Exception:
                pass
        return animado, activo

    def animacion_existe(self, ruta):
        """True / False, o None si no se pudo verificar."""
        bm = self.servicio("ALBehaviorManager")
        if bm is None:
            return None
        try:
            return bool(bm.isBehaviorInstalled(ruta))
        except Exception:
            return None

    # --- Movimiento ----------------------------------------------------------
    def de_pie(self):
        motion = self.servicio("ALMotion")
        posture = self.servicio("ALRobotPosture")
        if motion is None or posture is None:
            raise ErrorShow(u"%s no tiene ALMotion/ALRobotPosture." % self.etiqueta())
        motion.wakeUp()
        if not posture.goToPosture("Stand", 0.6):
            raise ErrorShow(u"%s no pudo llegar a la postura Stand." % self.etiqueta())

    def detener_habla(self):
        if self.sesion is None:
            return
        try:
            self._tts().stopAll()
        except Exception:
            pass

    def de_pie_seguro(self):
        if self.sesion is None:
            return
        try:
            self.servicio("ALRobotPosture").goToPosture("Stand", 0.5)
        except Exception:
            pass

    def toque_cabeza(self):
        try:
            return float(self.servicio("ALMemory").getData("FrontTactilTouched")) > 0.5
        except Exception:
            return False

    # --- Comportamientos (baile) ---------------------------------------------
    def comportamientos_instalados(self):
        bm = self.servicio("ALBehaviorManager")
        if bm is None:
            return []
        try:
            return [_texto(b) for b in bm.getInstalledBehaviors()]
        except Exception:
            return []

    def ejecutar_comportamiento(self, ruta):
        """runBehavior asíncrono: el futuro termina cuando termina el comportamiento."""
        bm = self.servicio("ALBehaviorManager")
        if bm is None:
            return FuturoListo(u"sin ALBehaviorManager")
        self._comportamiento = ruta
        try:
            if bm.isBehaviorRunning(_para_naoqi(ruta)):
                bm.stopBehavior(_para_naoqi(ruta))
            return FuturoQi(bm.runBehavior(_para_naoqi(ruta), _async=True))
        except Exception as e:
            return FuturoListo(_texto(e))

    def detener_comportamiento(self):
        """Detiene solo el comportamiento que lanzó este script (no stopAllBehaviors,
        para no matar el propio behavior si el show corre desde Choregraphe)."""
        if self.sesion is None or not self._comportamiento:
            return
        try:
            bm = self.servicio("ALBehaviorManager")
            if bm.isBehaviorRunning(_para_naoqi(self._comportamiento)):
                bm.stopBehavior(_para_naoqi(self._comportamiento))
        except Exception:
            pass

    def detener_audio(self):
        """Corta la música/sonidos que haya dejado sonando un comportamiento."""
        try:
            self.servicio("ALAudioPlayer").stopAll()
        except Exception:
            pass

    def volumen_efectos(self, volumen):
        """Volumen (0.0-1.0) de los sonidos de animaciones y comportamientos
        (ALAudioPlayer), p. ej. los aplausos de Winner_2. No afecta la voz:
        ALTextToSpeech sale por su propio camino."""
        try:
            self.servicio("ALAudioPlayer").setMasterVolume(float(volumen))
            return True
        except Exception:
            return False

    # --- Brazos (movimiento directo, sin sonido) ------------------------------
    def angulos_brazos(self):
        """Ángulos actuales de los brazos (para volver a ellos después)."""
        try:
            return [float(a) for a in self.servicio("ALMotion").getAngles(ARTICULACIONES_BRAZOS, True)]
        except Exception:
            return None

    def mover_brazos(self, angulos, duracion):
        """Lleva los brazos a 'angulos' en 'duracion' s, sin bloquear."""
        try:
            motion = self.servicio("ALMotion")
            f = motion.angleInterpolation(ARTICULACIONES_BRAZOS,
                                          [[float(a)] for a in angulos],
                                          [[float(duracion)]] * len(ARTICULACIONES_BRAZOS),
                                          True, _async=True)
            return FuturoQi(f)
        except Exception as e:
            return FuturoListo(_texto(e))

    def precargar_comportamiento(self, ruta):
        """Carga el comportamiento antes del show para que arranque sin demora."""
        try:
            return bool(self.servicio("ALBehaviorManager").preloadBehavior(_para_naoqi(ruta)))
        except Exception:
            return False

    def proteger_caidas(self):
        """Activa el FallManager de NAOqi (protege brazos y cabeza al caer)."""
        try:
            motion = self.servicio("ALMotion")
            motion.setFallManagerEnabled(True)
            return bool(motion.getFallManagerEnabled())
        except Exception:
            return None

    # --- Latencia y habla ----------------------------------------------------
    def medir_latencia(self, muestras):
        """Mediana del tiempo de ida y vuelta (s) de una llamada liviana."""
        tts = self._tts()
        try:
            tts.ping()
            llamada = tts.ping
        except Exception:
            llamada = tts.getVolume
        llamada()  # calentamiento
        tiempos = []
        for _ in range(max(1, muestras)):
            t0 = _ahora_real()
            llamada()
            tiempos.append(_ahora_real() - t0)
        return _mediana(tiempos)

    def decir(self, texto, animacion=None, modo_cuerpo="contextual", sin_gestos=False):
        """Lanza say() de forma asíncrona y devuelve un futuro."""
        frase = u"\\rspd=%d\\ %s" % (self.velocidad, texto)
        animado = None if sin_gestos else self.servicio("ALAnimatedSpeech")
        try:
            if animado is None:
                f = self._tts().say(_para_naoqi(frase), _async=True)
            else:
                if animacion:
                    frase = u"^start(%s) %s ^wait(%s)" % (animacion, frase, animacion)
                f = animado.say(_para_naoqi(frase),
                                {"bodyLanguageMode": modo_cuerpo}, _async=True)
            return FuturoQi(f)
        except Exception as e:
            return FuturoListo(_texto(e))


# =============================================================================
# ROBOT SIMULADO (--simular): misma interfaz, sin conexión
# =============================================================================
class RobotSimulado(object):
    def __init__(self, clave, ip, puerto, reloj, rtt):
        self.clave = clave
        self.nombre = NOMBRES[clave]
        self.ip = ip
        self.puerto = puerto
        self.sesion = True
        self.velocidad = VELOCIDAD
        self._reloj = reloj
        self._rtt = rtt
        self._azar = random.Random(len(clave) + int(rtt * 1e6))

    def etiqueta(self):
        return u"%s (%s, simulado)" % (self.nombre, _texto(self.ip))

    def conectar(self):
        pass

    def version(self):
        return u"2.8.6.23"

    def nombre_robot(self):
        return u"sim-" + self.clave.lower()

    def bateria(self):
        return 80 if self.clave == NAO1 else 65

    def configurar_voz(self, idioma, velocidad, volumen, volumen_tts):
        self.velocidad = int(velocidad)
        return {"idioma": _texto(idioma), "voz": u"(simulada)",
                "volumen": int(volumen), "volumen_tts": float(volumen_tts)}

    def ajustar_vida_autonoma(self, modo):
        if modo == "no-tocar":
            return [u"Vida autónoma sin cambios (simulado)"]
        return [u"BasicAwareness desactivado (simulado)",
                u"ListeningMovement desactivado (simulado)"]

    def estado_gestos(self):
        return True, True

    def animacion_existe(self, ruta):
        return True

    def de_pie(self):
        pass

    def detener_habla(self):
        pass

    def de_pie_seguro(self):
        pass

    def toque_cabeza(self):
        return True

    def comportamientos_instalados(self):
        if self.clave == NAO1:
            return [u"dancemoves-de295e/behavior_1", u"animations/Stand/Gestures/Hey_1"]
        return [u"naoacompaamiento-1bb1c2/behavior_1", u"animations/Stand/Gestures/Hey_1"]

    def ejecutar_comportamiento(self, ruta):
        # Arranca tras media latencia (ida), como el habla simulada
        duracion = self._rtt / 2.0 + (42.0 if u"dance" in ruta else 41.6)
        self._futuro_comp = FuturoSimulado(self._reloj, self._reloj.ahora() + duracion)
        return self._futuro_comp

    def detener_comportamiento(self):
        futuro = getattr(self, "_futuro_comp", None)
        if futuro is not None:
            futuro.cortar()

    def detener_audio(self):
        pass

    def volumen_efectos(self, volumen):
        return True

    def angulos_brazos(self):
        return [1.4, 0.2, -1.2, -0.5, 0.0, 0.3, 1.4, -0.2, 1.2, 0.5, 0.0, 0.3]

    def mover_brazos(self, angulos, duracion):
        return FuturoSimulado(self._reloj, self._reloj.ahora() + self._rtt / 2.0 + duracion)

    def precargar_comportamiento(self, ruta):
        return True

    def proteger_caidas(self):
        return True

    def medir_latencia(self, muestras):
        return _mediana([self._rtt * self._azar.uniform(0.8, 1.3)
                         for _ in range(max(1, muestras))])

    def decir(self, texto, animacion=None, modo_cuerpo="contextual", sin_gestos=False):
        # Empieza a hablar tras media latencia (ida) y dura lo estimado
        fin = (self._reloj.ahora() + self._rtt / 2.0
               + estimar_duracion(texto, self.velocidad) + self._azar.uniform(0, 0.01))
        return FuturoSimulado(self._reloj, fin)


# =============================================================================
# ORQUESTADOR DEL SHOW
# =============================================================================
class ShowCIIS(object):
    def __init__(self, opciones, log=None):
        self.op = opciones
        self._log_externo = log  # p. ej. self.logger.info en Choregraphe
        self._lock_log = threading.Lock()
        self._abortar = False
        self.simulado = bool(opciones.simular)
        if self.simulado and not opciones.simular_tiempo_real:
            self.reloj = RelojVirtual()
        else:
            self.reloj = RelojReal()
        if self.simulado:
            self.nao1 = RobotSimulado(NAO1, opciones.ip_nao1, opciones.puerto, self.reloj, 0.002)
            self.nao2 = RobotSimulado(NAO2, opciones.ip_nao2, opciones.puerto, self.reloj, 0.018)
        else:
            self.nao1 = Robot(NAO1, opciones.ip_nao1, opciones.puerto)
            self.nao2 = Robot(NAO2, opciones.ip_nao2, opciones.puerto)
        self.robots = {NAO1: self.nao1, NAO2: self.nao2}
        self.animaciones = {}                    # {clave_robot: {"saludo": ruta, ...}}
        self.retrasos_ambos = {NAO1: 0.0, NAO2: 0.0}  # s
        self.baile = {NAO1: None, NAO2: None}         # rutas resueltas en la preparación

    # --- Registro ------------------------------------------------------------
    def log(self, mensaje=u""):
        mensaje = _texto(mensaje)
        with self._lock_log:
            if self._log_externo is not None:
                self._log_externo(mensaje.encode("utf-8") if PY2 else mensaje)
            else:
                _imprimir(mensaje)

    def _titulo(self, texto):
        self.log(u"")
        self.log(u"=== %s ===" % texto)

    # --- Punto de entrada ----------------------------------------------------
    def correr(self):
        """Devuelve 0 si todo salió bien, 1 si hubo error, 130 si se detuvo."""
        try:
            self._validar()
            if self.simulado:
                self.log(u"*** MODO SIMULACIÓN: sin robots, tiempos ESTIMADOS ***")
            self._conectar()
            if self.op.identificar:
                self._identificar()
                return 0
            self._preparar()
            if not self._esperar_senal():
                return 1
            self._show()
            return 0
        except ErrorShow as e:
            self.log(u"ERROR: %s" % _texto(e))
            return 1
        except ShowInterrumpido:
            self.log(u"Show detenido.")
            return 130

    def detener(self):
        """Ctrl+C o parada desde Choregraphe: callar y dejar de pie."""
        self._abortar = True
        self.log(u"Deteniendo: silenciando robots y volviendo a Stand...")
        for r in (self.nao1, self.nao2):
            r.detener_habla()
            r.detener_comportamiento()
            r.detener_audio()
        self._en_paralelo([(r.clave, r.de_pie_seguro) for r in (self.nao1, self.nao2)],
                          timeout=15.0)
        self.log(u"Robots detenidos y de pie.")

    # --- Pasos ---------------------------------------------------------------
    def _validar(self):
        if not 1 <= self.op.desde <= len(GUION):
            raise ErrorShow(u"--desde debe estar entre 1 y %d." % len(GUION))
        if not self.simulado:
            for clave, ip in ((NAO1, self.op.ip_nao1), (NAO2, self.op.ip_nao2)):
                if not ip or ip.startswith("[") or ip.upper().startswith("IP_NAO"):
                    raise ErrorShow(u"Falta la IP de %s: edita la constante al inicio "
                                    u"del archivo o usa --ip-%s."
                                    % (NOMBRES[clave], clave.lower()))
            if self.op.ip_nao1 == self.op.ip_nao2:
                raise ErrorShow(u"NAO 1 y NAO 2 tienen la misma IP (%s)." % _texto(self.op.ip_nao1))

    def _conectar(self):
        self._titulo(u"1. Conexión")
        for r in (self.nao1, self.nao2):
            r.conectar()
            self.log(u"  %s conectado. Nombre: %s | NAOqi %s"
                     % (r.etiqueta(), r.nombre_robot(), r.version()))
        v1, v2 = self.nao1.version(), self.nao2.version()
        if v1 != v2:
            self.log(u"  AVISO: versiones de NAOqi distintas (%s / %s): el TTS podría "
                     u"durar distinto en la frase al unísono. Afinar offsets en el ensayo."
                     % (v1, v2))

    def _configurar_voz(self):
        for r in (self.nao1, self.nao2):
            info = r.configurar_voz(self.op.idioma, self.op.velocidad,
                                    self.op.volumen, self.op.volumen_tts)
            self.log(u"  %s: idioma %s, voz %s, velocidad %d%%, volumen %s, volumen TTS %.2f"
                     % (r.nombre, info["idioma"], info["voz"], r.velocidad,
                        info["volumen"], info["volumen_tts"]))

    def _identificar(self):
        self._titulo(u"Identificación")
        self._configurar_voz()
        for r, frase in ((self.nao1, u"Soy NAO uno"), (self.nao2, u"Soy NAO dos")):
            self.log(u"  %s dice: %s" % (r.etiqueta(), frase))
            fut = r.decir(frase, sin_gestos=True)
            self._esperar({r.clave: fut})
            if fut.error():
                self.log(u"  AVISO: %s" % fut.error())
            self.reloj.dormir(0.5)

    def _preparar(self):
        self._titulo(u"2. Batería")
        for r in (self.nao1, self.nao2):
            b = r.bateria()
            if b is None:
                self.log(u"  %s: batería desconocida" % r.nombre)
            elif b < BATERIA_MINIMA:
                self.log(u"  AVISO: %s con batería BAJA: %d %%" % (r.nombre, b))
            else:
                self.log(u"  %s: %d %%" % (r.nombre, b))

        self._titulo(u"3. Voz (mismo idioma, velocidad y volumen en ambos)")
        self._configurar_voz()

        self._titulo(u"4. Vida autónoma, gestos y postura")
        for r in (self.nao1, self.nao2):
            for m in r.ajustar_vida_autonoma(self.op.vida_autonoma):
                self.log(u"  %s: %s" % (r.nombre, m))
            if self.op.sin_gestos:
                continue
            animado, activo = r.estado_gestos()
            if not animado:
                self.log(u"  AVISO: %s no tiene ALAnimatedSpeech; hablará sin gestos." % r.nombre)
            elif activo is False:
                self.log(u"  AVISO: %s: SpeakingMovement desactivado; los gestos "
                         u"contextuales podrían no verse." % r.nombre)
            else:
                self.log(u"  %s: gestos al hablar OK (SpeakingMovement %s)"
                         % (r.nombre, u"activo" if activo else u"sin verificar"))
        self.log(u"  wakeUp + postura Stand en ambos...")
        _, errores = self._en_paralelo([(r.clave, r.de_pie) for r in (self.nao1, self.nao2)])
        for clave, e in errores.items():
            raise ErrorShow(u"%s: %s" % (self.robots[clave].etiqueta(), _texto(e)))
        self.log(u"  Ambos de pie.")

        self._titulo(u"5. Animaciones y baile")
        self._verificar_animaciones()
        self._verificar_bailes()

        self._titulo(u"6. Latencia y sincronización de la frase AMBOS")
        self._medir_latencias()

        self._titulo(u"7. Resumen")
        lineas = [l for l in GUION[self.op.desde - 1:] if self._linea_activa(l)]
        estimado = sum(estimar_duracion(texto_para_tts(l), self.op.velocidad) for l in lineas)
        for l in lineas[1:]:
            estimado += self.op.pausa_ambos if l["hablante"] == AMBOS else self.op.pausa
        if not self.op.sin_gestos and any(l["hablante"] == AMBOS for l in lineas):
            estimado += TIEMPO_BAJAR_BRAZOS  # se espera a que bajen antes de seguir
        self.log(u"  Líneas %d a %d, discurso estimado: %.1f s (objetivo %d-%d s)%s"
                 % (self.op.desde, len(GUION), estimado,
                    DURACION_OBJETIVO[0], DURACION_OBJETIVO[1],
                    u" + baile" if self.baile[NAO1] else u""))

    def _linea_activa(self, linea):
        return linea["requiere"] != "baile" or self.baile[NAO1] is not None

    def _verificar_bailes(self):
        self.baile = {NAO1: None, NAO2: None}
        if self.op.sin_baile:
            self.log(u"  --sin-baile: se omiten el anuncio y el baile.")
            return
        ruta1 = resolver_comportamiento(self.nao1.comportamientos_instalados(), self.op.baile_nao1)
        if ruta1 is None:
            self.log(u"  AVISO: NAO 1 no tiene instalado '%s'; se omiten el anuncio y el baile."
                     % _texto(self.op.baile_nao1))
            return
        ruta2 = resolver_comportamiento(self.nao2.comportamientos_instalados(), self.op.baile_nao2)
        if ruta2 is None:
            self.log(u"  AVISO: NAO 2 no tiene instalado '%s'; se quedará de pie mientras baila NAO 1."
                     % _texto(self.op.baile_nao2))
        for r, ruta in ((self.nao1, ruta1), (self.nao2, ruta2)):
            if ruta is None:
                continue
            precargado = r.precargar_comportamiento(ruta)
            self.log(u"  Baile %s: %s (%s)" % (r.nombre, ruta,
                                               u"precargado" if precargado else u"sin precargar"))
        if self.op.duracion_max_baile > 0:
            self.log(u"  El baile se cortará a los %g s." % self.op.duracion_max_baile)
        self.baile[NAO2] = ruta2
        for r in (self.nao1, self.nao2):
            activo = r.proteger_caidas()
            self.log(u"  %s: protección de caídas (FallManager) %s"
                     % (r.nombre, {True: u"activa", False: u"DESACTIVADA", None: u"sin verificar"}[activo]))
        self.baile[NAO1] = ruta1

    def _verificar_animaciones(self):
        if self.op.sin_gestos:
            self.log(u"  --sin-gestos: solo ALTextToSpeech, sin animaciones.")
            return
        saludo = [self.op.animacion_saludo] if self.op.animacion_saludo else ANIMACIONES_SALUDO
        for r in (self.nao1, self.nao2):
            self.animaciones[r.clave] = {}
        # Saludo: solo lo hace NAO 1 (línea 1)
        self.animaciones[NAO1]["saludo"] = self._primera_existente([self.nao1], saludo, u"saludo")
        self.log(u"  Frase AMBOS: brazos arriba con motores (ALMotion), sin animación ni sonidos")

    def _primera_existente(self, robots, candidatas, descripcion):
        for ruta in candidatas:
            resultados = [r.animacion_existe(ruta) for r in robots]
            if all(res is True for res in resultados):
                self.log(u"  Animación de %s: %s (en %s)"
                         % (descripcion, ruta, u" y ".join(r.nombre for r in robots)))
                return ruta
        self.log(u"  AVISO: ninguna animación de %s disponible en %s; se habla sin ella."
                 % (descripcion, u" y ".join(r.nombre for r in robots)))
        return None

    def _medir_latencias(self):
        latencias = {}  # ida (s) = RTT / 2
        for r in (self.nao1, self.nao2):
            rtt = r.medir_latencia(self.op.muestras_latencia)
            latencias[r.clave] = rtt / 2.0
            self.log(u"  %s: RTT mediano %.1f ms (%d muestras) -> ida ~%.1f ms"
                     % (r.nombre, rtt * 1000, self.op.muestras_latencia, rtt * 500))
        maxima = max(latencias.values())
        offsets = {NAO1: self.op.offset_nao1 / 1000.0, NAO2: self.op.offset_nao2 / 1000.0}
        # El robot con menor latencia se retrasa la diferencia; luego offsets manuales
        base = dict((k, (maxima - latencias[k]) + offsets[k]) for k in latencias)
        minimo = min(base.values())
        self.retrasos_ambos = dict((k, base[k] - minimo) for k in base)
        for k in (NAO1, NAO2):
            self.log(u"  %s: compensación %.1f ms + offset manual %+.0f ms => se lanza %.1f ms después"
                     % (NOMBRES[k], (maxima - latencias[k]) * 1000, offsets[k] * 1000,
                        self.retrasos_ambos[k] * 1000))

    def _esperar_senal(self):
        modo = self.op.senal
        self._titulo(u"LISTO")
        if modo == "ninguna":
            self.log(u"  Empezando sin esperar señal.")
            return True
        if modo == "cabeza":
            self.log(u"LISTO: toca la cabeza (sensor delantero) del NAO 1 para empezar")
            while not self.nao1.toque_cabeza():
                if self._abortar:
                    raise ShowInterrumpido()
                time.sleep(0.05)
            return True
        self.log(u"LISTO: presiona ENTER para empezar")
        try:
            leer_linea()
        except EOFError:
            self.log(u"ERROR: no hay consola para leer ENTER. Usa --senal cabeza o --senal ninguna.")
            return False
        return True

    # --- Show ----------------------------------------------------------------
    def _show(self):
        self._titulo(u"SHOW")
        t_inicio = self.reloj.ahora()
        fin_anterior = None
        fin_discurso = None
        for numero in range(self.op.desde, len(GUION) + 1):
            linea = GUION[numero - 1]
            if not self._linea_activa(linea):
                self.log(u"  (línea %d omitida: no hay baile disponible)" % numero)
                continue
            if fin_anterior is not None:
                pausa = self.op.pausa_ambos if linea["hablante"] == AMBOS else self.op.pausa
                self.reloj.dormir_hasta(fin_anterior + pausa)
            if self._abortar:
                raise ShowInterrumpido()
            if linea["hablante"] == AMBOS:
                fin_anterior = self._linea_ambos(numero, linea, t_inicio)
            else:
                fin_anterior = self._linea_turno(numero, linea, t_inicio)
            fin_discurso = fin_anterior
            if linea["requiere"] == "baile":
                self.reloj.dormir_hasta(fin_anterior + self.op.pausa)
                fin_anterior = self._baile(t_inicio)
        fin = self.reloj.ahora()
        discurso = (fin_discurso if fin_discurso is not None else fin) - t_inicio
        if self.op.desde > 1:
            aviso = u"(show parcial desde la línea %d)" % self.op.desde
        elif DURACION_OBJETIVO[0] <= discurso <= DURACION_OBJETIVO[1]:
            aviso = u"OK"
        else:
            aviso = u"FUERA DEL OBJETIVO"
        estimada = u" ESTIMADA" if self.simulado else u""
        self.log(u"")
        self.log(u"Duración del discurso%s: %.1f s (objetivo %d-%d s) %s"
                 % (estimada, discurso, DURACION_OBJETIVO[0], DURACION_OBJETIVO[1], aviso))
        if fin_discurso is not None and fin > fin_discurso + self.op.pausa:
            self.log(u"Duración total con baile%s: %.1f s" % (estimada, fin - t_inicio))

    def _baile(self, t_inicio):
        """NAO 1 y NAO 2 bailan JUNTOS: sus comportamientos arrancan en el mismo
        instante (misma compensación de latencia y offsets que la frase AMBOS).
        El baile termina cuando terminan los dos (o al cumplirse --duracion-max-baile)."""
        rutas = {NAO1: self.baile[NAO1], NAO2: self.baile[NAO2]}
        self.log(u"[%s] +%6.2fs  BAILE  NAO 1: %s | NAO 2: %s"
                 % (self.reloj.hora(), self.reloj.ahora() - t_inicio, rutas[NAO1],
                    rutas[NAO2] or u"(de pie)"))
        activos = [r for r in (self.nao1, self.nao2) if rutas[r.clave]]
        # Se lanza primero el robot con menor retraso; el otro, a su hora exacta
        orden = sorted(activos, key=lambda r: self.retrasos_ambos[r.clave])
        t_base = self.reloj.ahora()
        lanzado, futuros = {}, {}
        for r in orden:
            self.reloj.dormir_hasta(t_base + self.retrasos_ambos[r.clave])
            lanzado[r.clave] = self.reloj.ahora()
            futuros[r.clave] = r.ejecutar_comportamiento(rutas[r.clave])
        for r in activos:
            self.log(u"      %s: lanzado +%.1f ms" % (r.nombre, (lanzado[r.clave] - t_base) * 1000))

        limite = None
        if self.op.duracion_max_baile > 0:
            limite = t_base + self.op.duracion_max_baile
        fines = {}
        while len(fines) < len(futuros):
            if self._abortar:
                raise ShowInterrumpido()
            ahora = self.reloj.ahora()
            for clave, f in futuros.items():
                if clave in fines or not f.terminado():
                    continue
                fines[clave] = ahora
                duracion = ahora - lanzado[clave]
                error = f.error()
                if error and duracion < 1.0:
                    self.log(u"      AVISO: %s no pudo ejecutar su baile: %s" % (NOMBRES[clave], error))
                else:
                    self.log(u"      +%6.2fs %s terminó su baile (%.1f s)"
                             % (ahora - t_inicio, NOMBRES[clave], duracion))
            if limite is not None and ahora >= limite and len(fines) < len(futuros):
                self.log(u"      +%6.2fs Tiempo de baile cumplido (%g s): se cortan bailes y música."
                         % (ahora - t_inicio, self.op.duracion_max_baile))
                for clave in futuros:
                    if clave not in fines:
                        self.robots[clave].detener_comportamiento()
                limite = None
            self.reloj.dormir(0.05)
        if self._abortar:  # detener() cortó el baile: no es un final normal
            raise ShowInterrumpido()
        if len(fines) == 2:
            self.log(u"      Desfase al terminar (NAO 2 - NAO 1): %+.0f ms"
                     % ((fines[NAO2] - fines[NAO1]) * 1000))
        for r in (self.nao1, self.nao2):  # que no quede música sonando
            r.detener_audio()
        _, errores = self._en_paralelo([(r.clave, r.de_pie) for r in (self.nao1, self.nao2)],
                                       timeout=20.0)
        for clave, e in errores.items():
            self.log(u"      AVISO: %s no volvió a Stand: %s" % (NOMBRES[clave], _texto(e)))
        self.log(u"      Ambos de pie.")
        return self.reloj.ahora()

    def _cabecera(self, numero, linea, t_inicio):
        self.log(u"[%s] +%6.2fs  L%02d %-5s: %s"
                 % (self.reloj.hora(), self.reloj.ahora() - t_inicio, numero,
                    NOMBRES[linea["hablante"]], linea["texto"]))

    def _animacion(self, robot, linea):
        if self.op.sin_gestos or not linea["animacion"]:
            return None
        return self.animaciones.get(robot.clave, {}).get(linea["animacion"])

    def _linea_turno(self, numero, linea, t_inicio):
        robot = self.robots[linea["hablante"]]
        texto = texto_para_tts(linea)
        self._cabecera(numero, linea, t_inicio)
        t0 = self.reloj.ahora()
        fut = robot.decir(texto, self._animacion(robot, linea), "contextual", self.op.sin_gestos)
        fin = self._esperar({robot.clave: fut})[robot.clave]
        if fut.error():
            # Plan B: repetir solo con ALTextToSpeech para que el show siga
            self.log(u"      AVISO: %s falló (%s); repitiendo sin gestos" % (robot.nombre, fut.error()))
            fut = robot.decir(texto, sin_gestos=True)
            fin = self._esperar({robot.clave: fut})[robot.clave]
        self.log(u"      duración %.2f s" % (fin - t0))
        return fin

    def _linea_ambos(self, numero, linea, t_inicio):
        texto = texto_para_tts(linea)
        self._cabecera(numero, linea, t_inicio)
        # Se lanza primero el robot con menor retraso; el otro, a su hora exacta
        orden = sorted((self.nao1, self.nao2), key=lambda r: self.retrasos_ambos[r.clave])
        # Brazos arriba con motores (sin animación, sin sonido), salvo --sin-gestos.
        # Se guarda la postura actual antes de t_base para no afectar la sincronización.
        reposo = {}
        if not self.op.sin_gestos:
            for r in (self.nao1, self.nao2):
                reposo[r.clave] = r.angulos_brazos()
        t_base = self.reloj.ahora()
        lanzado, futuros, brazos = {}, {}, {}
        for r in orden:
            self.reloj.dormir_hasta(t_base + self.retrasos_ambos[r.clave])
            lanzado[r.clave] = self.reloj.ahora()
            if r.clave in reposo:
                brazos[r.clave] = r.mover_brazos(BRAZOS_ARRIBA, TIEMPO_SUBIR_BRAZOS)
            # bodyLanguageMode "disabled": sin gestos automáticos que choquen con los brazos
            futuros[r.clave] = r.decir(texto, None, "disabled", self.op.sin_gestos)
        fines = self._esperar(futuros)
        if brazos:
            self._esperar(brazos)  # normalmente ya terminaron de subir
            for clave, f in brazos.items():
                if f.error():
                    self.log(u"      AVISO: %s no pudo levantar los brazos: %s" % (NOMBRES[clave], f.error()))
            # Al terminar la frase, bajan los dos juntos a la postura que tenían
            bajada = {}
            for r in (self.nao1, self.nao2):
                if reposo.get(r.clave):
                    bajada[r.clave] = r.mover_brazos(reposo[r.clave], TIEMPO_BAJAR_BRAZOS)
            self._esperar(bajada)
            self.log(u"      Brazos arriba durante la frase y abajo al terminar (%s)"
                     % u" y ".join(NOMBRES[k] for k in sorted(brazos)))
        for r in (self.nao1, self.nao2):
            err = futuros[r.clave].error()
            self.log(u"      %s: lanzado +%.1f ms, terminó %s (+%.2fs), duración %.2f s%s"
                     % (r.nombre, (lanzado[r.clave] - t_base) * 1000,
                        self._hora_de(fines[r.clave]), fines[r.clave] - t_inicio,
                        fines[r.clave] - lanzado[r.clave],
                        u"  ERROR: %s" % err if err else u""))
        self.log(u"      Desfase al terminar (NAO 2 - NAO 1): %+.0f ms"
                 % ((fines[NAO2] - fines[NAO1]) * 1000))
        return max(fines.values())

    def _hora_de(self, t):
        """Hora de reloj de pared correspondiente al instante t del reloj interno."""
        delta = datetime.timedelta(seconds=t - self.reloj.ahora())
        if isinstance(self.reloj, RelojVirtual):
            base = self.reloj._inicio + datetime.timedelta(seconds=self.reloj.ahora())
        else:
            base = datetime.datetime.now()
        return (base + delta).strftime("%H:%M:%S.%f")[:-3]

    def _esperar(self, futuros):
        """Espera a que terminen los futuros; devuelve {clave: instante de fin}."""
        fines = {}
        limite = self.reloj.ahora() + TIEMPO_MAXIMO_LINEA
        while len(fines) < len(futuros):
            if self._abortar:
                raise ShowInterrumpido()
            for clave, f in futuros.items():
                if clave not in fines and f.terminado():
                    fines[clave] = self.reloj.ahora()
            if len(fines) < len(futuros):
                if self.reloj.ahora() > limite:
                    for clave in futuros:
                        if clave not in fines:
                            self.log(u"      AVISO: %s no terminó en %d s; se continúa."
                                     % (NOMBRES[clave], TIEMPO_MAXIMO_LINEA))
                            fines[clave] = self.reloj.ahora()
                    break
                self.reloj.dormir(0.005)
        return fines

    def _en_paralelo(self, tareas, timeout=60.0):
        """Ejecuta [(clave, funcion)] en hilos; devuelve (resultados, errores)."""
        resultados, errores = {}, {}

        def envolver(clave, funcion):
            try:
                resultados[clave] = funcion()
            except Exception as e:
                errores[clave] = e

        hilos = []
        for clave, funcion in tareas:
            h = threading.Thread(target=envolver, args=(clave, funcion))
            h.daemon = True
            h.start()
            hilos.append(h)
        limite = time.time() + timeout
        for h in hilos:
            while h.is_alive() and time.time() < limite:
                h.join(0.1)  # join con timeout: Ctrl+C sigue funcionando en Python 2
        return resultados, errores


# =============================================================================
# ARGUMENTOS Y MAIN
# (textos de ayuda sin tildes para evitar problemas de codificación en Python 2)
# =============================================================================
def parsear_argumentos(argv=None):
    p = argparse.ArgumentParser(description="Show de inauguracion CIIS con dos NAO V6.")
    p.add_argument("--ip-nao1", default=NAO1_IP, help="IP del NAO 1 (127.0.0.1 si corre dentro del NAO 1)")
    p.add_argument("--ip-nao2", default=NAO2_IP, help="IP del NAO 2")
    p.add_argument("--puerto", type=int, default=PUERTO_NAOQI, help="Puerto NAOqi (9559)")
    p.add_argument("--pausa", type=float, default=PAUSA_ENTRE_LINEAS,
                   help="Pausa en s entre lineas (%(default)s)")
    p.add_argument("--pausa-ambos", type=float, default=PAUSA_ANTES_DE_AMBOS,
                   help="Pausa en s entre el 'tres' y la frase AMBOS (%(default)s)")
    p.add_argument("--offset-nao1", type=float, default=0.0,
                   help="ms extra de retraso del NAO 1 en la frase AMBOS (puede ser negativo)")
    p.add_argument("--offset-nao2", type=float, default=0.0,
                   help="ms extra de retraso del NAO 2 en la frase AMBOS (puede ser negativo)")
    p.add_argument("--muestras-latencia", type=int, default=MUESTRAS_LATENCIA,
                   help="Llamadas para medir latencia (%(default)s)")
    p.add_argument("--idioma", default=IDIOMA, help="Idioma TTS (%(default)s)")
    p.add_argument("--velocidad", type=int, default=VELOCIDAD, help="Velocidad del habla en %% (%(default)s)")
    p.add_argument("--volumen", type=int, default=VOLUMEN, help="Volumen general 0-100 (%(default)s)")
    p.add_argument("--volumen-tts", type=float, default=VOLUMEN_TTS, help="Volumen TTS 0-1 (%(default)s)")
    p.add_argument("--sin-gestos", action="store_true", help="Solo ALTextToSpeech, sin gestos")
    p.add_argument("--vida-autonoma", choices=["ajustar", "no-tocar", "desactivar"], default="ajustar",
                   help="ajustar: apaga BasicAwareness/ListeningMovement y mantiene los gestos al "
                        "hablar (por defecto); no-tocar: no cambia nada; desactivar: Autonomous "
                        "Life en 'disabled'")
    p.add_argument("--animacion-saludo", default=None, help="Ruta de animacion para el saludo")
    p.add_argument("--sin-baile", action="store_true",
                   help="Omitir el anuncio 'Ahora para celebrar, un baile' y el baile")
    p.add_argument("--baile-nao1", default=BAILE_NAO1,
                   help="Comportamiento de baile del NAO 1: ruta o parte del nombre (%(default)s)")
    p.add_argument("--baile-nao2", default=BAILE_NAO2,
                   help="Comportamiento de baile del NAO 2, se baila junto al del NAO 1 (%(default)s)")
    p.add_argument("--duracion-max-baile", type=float, default=DURACION_MAXIMA_BAILE,
                   help="Si es > 0, a esos segundos se cortan bailes y musica "
                        "(0 = hasta que terminen) (%(default)s)")
    p.add_argument("--senal", choices=["enter", "cabeza", "ninguna"], default="enter",
                   help="Senal de inicio: ENTER, toque en la cabeza del NAO 1, o ninguna")
    p.add_argument("--identificar", action="store_true", help="Cada robot dice 'Soy NAO uno/dos'")
    p.add_argument("--desde", type=int, default=1, help="Empezar desde la linea N (1-%d)" % len(GUION))
    p.add_argument("--simular", action="store_true", help="Sin robots: tiempos estimados")
    p.add_argument("--simular-tiempo-real", action="store_true",
                   help="Con --simular: esperar los tiempos de verdad")
    return p.parse_args(argv)


def main(argv=None):
    opciones = parsear_argumentos(argv)
    show = ShowCIIS(opciones)
    try:
        return show.correr()
    except KeyboardInterrupt:
        show.log(u"")
        show.log(u"Ctrl+C recibido.")
        try:
            show.detener()
        except KeyboardInterrupt:
            pass
        return 130


if __name__ == "__main__":
    sys.exit(main())
