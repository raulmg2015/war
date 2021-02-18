from rasa_sdk import Action, Tracker
from rasa_sdk.forms import FormAction
from rasa_sdk.events import EventType, SlotSet, UserUtteranceReverted, AllSlotsReset, Restarted
from rasa_sdk.executor import CollectingDispatcher

from typing import Any, Text, Dict, List, Union, Optional
from datetime import datetime
from ast import literal_eval
from redis import Redis

import os
import re
import json
import random
import logging
import unidecode

from utils import imss, quejas, estados, compare, quota, age, mock_data, momentum, audio

# REDIS
redis_host = os.environ.get("REDIS_HOSTNAME", "localhost")
redis_pass = os.environ.get("REDIS_PASSWORD", "")
redis = Redis(
    host=redis_host,
    password=redis_pass,
    decode_responses=True
)
data_ex = 3600

# FLAGS
skip_oncologia = True if os.environ.get("SKIP_ONCOLOGIA", "") == "true" else False
skip_vigencia = True if os.environ.get("SKIP_VIGENCIA", "true") == "true" else False
mock = True if os.environ.get("MOCK", "") == "true" else False

logging.basicConfig(level=logging.INFO)


def extract_metadata_from_tracker(tracker: Tracker) -> Dict:
    events = tracker.current_state().get("events", [])
    user_events = [e for e in events if e.get("event", "") == "user"]
    if not user_events:
        return {}
    else:
        return user_events[-1].get("metadata", {})


def remove_style(string: str):
    if string.count("*") % 2 == 0:
        string = string.replace("*", "")
    if string.count("```") % 2 == 0:
        string = string.replace("```", "")
    if string.count("_") % 2 == 0:
        string = string.replace("_", "")
    if string.count("~") % 2 == 0:
        string = string.replace("~", "")
    return string

def strip_accents(string: str) -> str:
    return unidecode.unidecode(string)

def equals(string_1: str, string_2: str) -> bool:
    return strip_accents(string_1).upper().strip() == strip_accents(string_2).upper().strip()

def check_paciente(
    nombre_paciente: str,
    nombre_paciente_real: str,
    apellido_paterno_paciente_real: str,
    apellido_materno_paciente_real: str
) -> bool:
    nombre_paciente = strip_accents(nombre_paciente).upper()
    nombre_paciente_real = strip_accents(nombre_paciente_real).upper()
    apellido_paterno_paciente_real = strip_accents(apellido_paterno_paciente_real).upper()
    apellido_materno_paciente_real = strip_accents(apellido_materno_paciente_real).upper()
    if nombre_paciente == nombre_paciente_real:
        return True
    elif nombre_paciente in nombre_paciente_real.split():
        return True
    elif set(nombre_paciente.split()) == set(nombre_paciente_real.split() + [apellido_paterno_paciente_real]):
        return True
    elif set(nombre_paciente.split()) == set(nombre_paciente_real.split() + [apellido_materno_paciente_real]):
        return True
    elif nombre_paciente == apellido_paterno_paciente_real:
        return False
    elif nombre_paciente == apellido_materno_paciente_real:
        return False
    elif set(nombre_paciente.split()).issubset(set(nombre_paciente_real.split() + [apellido_paterno_paciente_real, apellido_materno_paciente_real])):
        return True
    return False


class QuejasForm(FormAction):
    def name(self) -> Text:
        return "quejas_form"

    @staticmethod
    def required_slots(tracker: Tracker) -> List[Text]:
        return [
            "nss",
            "queja_id",
            "nombre",
            "confirmar_estado",
            "estado",
            "ciudad",
            "unidad_imss_opciones",
            "unidad_imss",
            "nombre_beneficiario",
            "categoria",
            "medicamento_opciones",
            "confirmar_medicamento",
            "queja",
        ]

    def extract_other_slots(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        return {}

    def slot_mappings(self) -> Dict[Text, Union[Dict, List[Dict]]]:
        return {
            "nss": [
                self.from_entity(entity="nss", intent="nss"),
                self.from_text(),
            ],
            "queja_id": [
                self.from_text(),
            ],
            "nombre": [
                self.from_text(),
            ],
            "nombre_beneficiario": [
                self.from_text(),
            ],
            "confirmar_estado": [
                self.from_text(),
            ],
            "estado": [
                self.from_text(),
            ],
            "ciudad": [
                self.from_text(),
            ],
            "unidad_imss_opciones": [
                self.from_text(),
            ],
            "unidad_imss": [
                self.from_text(),
            ],
            "categoria": [
                self.from_text(),
            ],
            "medicamento_opciones": [
                self.from_text(),
            ],
            "confirmar_medicamento": [
                self.from_text(),
            ],
            "queja": [
                self.from_text(),
            ]
        }

    def validate_nss(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if not isinstance(value, str):
            dispatcher.utter_message(template="utter_nss_incorrecto")
            return {"nss": None}

        value = remove_style(value)
        alta_apo = None

        nss_regex = r"[0-9]{10,11}"
        check_nss = re.findall(nss_regex, value)
        if check_nss and value.isdecimal() and (len(value) == 11 or len(value) == 10):
            if mock:
                asegurado = mock_data.MOCK_NSS_DATA
                error = None
            else:
                asegurado, error = imss.consultar_por_nss(value)

            if asegurado:
                # Vigencia
                vigente_hasta = datetime.strptime(asegurado.get("VigenteHasta", "3000/01/01"), "%Y/%m/%d")
                valid = vigente_hasta >= datetime.now()

                # UMF
                dumf = asegurado.get("DhDeleg", "")
                if dumf:
                    estado = estados.cat_estados[estados.umf_delegaciones_estados.get(dumf, 0)]
                else:
                    estado = None

                confirmar_estado = False if not estado else None

                # Nombre
                nombre = asegurado.get("Nombre", "").title()

                if valid or skip_vigencia:
                    if mock:
                        beneficiarios_oncologia = mock_data.MOCK_APOP_DATA
                    else:
                        beneficiarios_oncologia = imss.verificar_oncologia(value)

                    # Sólo menores de edad
                    if beneficiarios_oncologia:
                        beneficiarios_oncologia = [
                            b for b in beneficiarios_oncologia
                            if age.is_underage(datetime.strptime(
                                b.get("fechaNacimiento", "3000-01-01")[:10],
                                "%Y-%m-%d"
                            ))
                        ]
                    else:
                        # Beneficiarios menores de edad (en caso de no haber beneficiarios en oncología)
                        beneficiarios_oncologia = [
                            {
                                "nombre": b.get("Nombre", ""),
                                "primerApellido": b.get("Paterno", ""),
                                "segundoApellido": b.get("Materno", "")
                            } for b in asegurado.get("Beneficiarios", [])
                            if age.is_underage(
                                datetime.strptime(
                                    b.get("FechaNacimiento", "3000/01/01")
                                    [:10], "%Y/%m/%d"))
                        ]
                        alta_apo = "false"

                    if beneficiarios_oncologia or skip_oncologia:

                        value = value[:10]
                        solicitudes_nss = quejas.get_quejas_pendientes_nss(value)
                        # solicitudes_nss = [] # MOCK
                        queja_id = None if solicitudes_nss else ""

                        return {
                            "nss": value,
                            "nombre": nombre,
                            "estado": estado,
                            "confirmar_estado": confirmar_estado,
                            "nombre_beneficiario_opciones": json.dumps(beneficiarios_oncologia),
                            "solicitud_opciones": json.dumps(solicitudes_nss),
                            "queja_id": queja_id,
                            "alta_apo": alta_apo,
                        }
                    else:
                        dispatcher.utter_message(template="utter_no_oncologia")
                        return {"nss": None}
                else:
                    dispatcher.utter_message(template="utter_no_derechos")
                    return {"nss": None}
            else:
                if error:
                    dispatcher.utter_message(template="utter_error_detalle", error=error)
                else:
                    dispatcher.utter_message(template="utter_error")
                return {"nss": None}

        else:
            dispatcher.utter_message(template="utter_nss_incorrecto")
            return {"nss": None}

    def validate_nombre_beneficiario(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = remove_style(value)

        beneficiarios = json.loads(tracker.get_slot("nombre_beneficiario_opciones"))
        nombres = [b.get("nombre") for b in beneficiarios]
        apellidos_paternos = [b.get("primerApellido") for b in beneficiarios]
        apellidos_maternos = [b.get("segundoApellido") for b in beneficiarios]

        if len(nombres) > 1:
            if value.isdecimal():
                val = int(value)
                if 0 < val <= len(nombres):
                    nombre_beneficiario = nombres[val-1]
                    diagnostico = beneficiarios[val-1].get("desCie10", "")
                    return {
                        "nombre_beneficiario": nombre_beneficiario.title(),
                        "diagnostico": diagnostico
                    }

            dispatcher.utter_message(template="utter_numero_incorrecto")
            return {"nombre_beneficiario": None}

        else:
            nombre = nombres[0]
            apellido_paterno = apellidos_paternos[0]
            apellido_materno = apellidos_maternos[0]

            if check_paciente(value, nombre, apellido_paterno, apellido_materno):
                diagnostico = beneficiarios[0].get("desCie10", "")
                return {
                    "nombre_beneficiario": nombre.title(),
                    "diagnostico": diagnostico
                }

            if str(tracker.get_slot("alta_apo")) == "false":
                dispatcher.utter_message(template="utter_nombre_beneficiario_incorrecto_no_apo")
            else:
                dispatcher.utter_message(template="utter_nombre_beneficiario_incorrecto")

            return {"nombre_beneficiario": None}

        return {"nombre_beneficiario": None}

    def validate_queja_id(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = remove_style(value)

        solicitudes = json.loads(tracker.get_slot("solicitud_opciones"))

        if value.isdecimal():
            val = int(value)
            if 0 < val <= len(solicitudes):

                solicitud = solicitudes[val-1]

                queja_id = solicitud.get("quejaId", 0)
                nombre = solicitud.get("nombre", "")
                nombre_beneficiario = solicitud.get("nombrePaciente", "")
                confirmar_estado = ""
                estado = solicitud.get("entidadFederativa", "")
                ciudad = solicitud.get("municipio", "")
                unidad_imss_opciones = ""
                unidad_imss = solicitud.get("cveClues", "")
                categoria = solicitud.get("categoriaId", 1)
                medicamento_opciones = ""
                confirmar_medicamento = ""

                # temp fix
                unidad_imss = "" if unidad_imss is None else unidad_imss

                dispatcher.utter_message(template="utter_escribir_quejas")
                redis.setex(tracker.sender_id+":message_flag", data_ex, "true")

                return {
                    "queja_id": queja_id,
                    "nombre": nombre,
                    "nombre_beneficiario": nombre_beneficiario,
                    "confirmar_estado": confirmar_estado,
                    "estado": estado,
                    "ciudad": ciudad,
                    "unidad_imss_opciones": unidad_imss_opciones,
                    "unidad_imss": unidad_imss,
                    "categoria": categoria,
                    "medicamento_opciones": medicamento_opciones,
                    "confirmar_medicamento": confirmar_medicamento,
                    "solicitud_opciones": "[]"
                }

        elif equals(value, "N"):
            if len(solicitudes) >= quota.quota_quejas_nss:
                dispatcher.utter_message(template="utter_quota_nss")
                return {"queja_id": None}
            return {
                "queja_id": "",
                "solicitud_opciones": "[]",
            }

        dispatcher.utter_message(template="utter_numero_incorrecto")
        return {"queja_id": None}

    def validate_confirmar_estado(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = remove_style(value)

        if equals(value, "SI"):
            return {"confirmar_estado": True}
        elif equals(value, "NO"):
            return {"confirmar_estado": False, "estado": None}

        return {"confirmar_estado": None}

    def validate_estado(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = remove_style(value)

        sim = compare.get_most_similar(strip_accents(value).upper(), tuple(estados.cat_estados_norm))
        if sim:
            sim = estados.cat_estados[estados.cat_estados_norm.index(sim)]
            # If estado is "Ciudad de México", "DF" or "CDMX", replace with "Distrito Federal"
            if sim == estados.cat_estados[-1] or sim == estados.cat_estados[-2] or sim == estados.cat_estados[-3]:
                sim = estados.cat_estados[0]
            return {"estado": sim}

        dispatcher.utter_message(template="utter_estado_incorrecto")
        return {"estado": None}

    def validate_ciudad(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = remove_style(value)

        return {"ciudad": value}

    def validate_unidad_imss_opciones(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if not isinstance(value, str):
            return {"unidad_imss_opciones": None}

        value = remove_style(value)

        if len(value) < 5:
            dispatcher.utter_message(template="utter_unidad_no_informacion")
            return {"unidad_imss_opciones": None}

        estado = tracker.get_slot("estado")
        full_cat = quejas.get_unidades_delegacion(estados.cve_estados.get(estado, ""))
        cat = [c.get("nomUnidad", "") for c in full_cat]
        cat_norm = [strip_accents(c).upper() for c in cat]

        coincidences = compare.get_coincidences_unidades(strip_accents(value).upper(), tuple(cat_norm))
        if strip_accents(value).upper() in cat_norm: #Priority
            cat = {
                strip_accents(c.get("nomUnidad")).upper(): c.get("cveClues")
                for c in full_cat
            }
            return {"unidad_imss_opciones": "", "unidad_imss": cat[strip_accents(value).upper()]}
        elif len(coincidences) == 0:
            dispatcher.utter_message(template="utter_unidad_no_encontrada")
            return {"unidad_imss_opciones": None}
        elif len(coincidences) > compare.CHOICE_LIMIT_UNIDADES:
            dispatcher.utter_message(template="utter_unidad_no_informacion")
            return {"unidad_imss_opciones": None}

        coincidences = [cat[cat_norm.index(c)] for c in coincidences]
        return {
            "unidad_imss_opciones": json.dumps(coincidences),
            "unidad_imss": None,
        }

    def validate_unidad_imss(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if not isinstance(value, str):
            return {"unidad_imss": None}

        value = remove_style(value)

        estado = tracker.get_slot("estado")
        full_cat = quejas.get_unidades_delegacion(estados.cve_estados.get(estado, ""))

        cat = {
            c.get("nomUnidad"): c.get("cveClues")
            for c in full_cat
        }

        unidades = json.loads(tracker.get_slot("unidad_imss_opciones"))
        if len(unidades) == 1:
            if equals(value, "SI"):
                return {"unidad_imss": cat[unidades[0]]}
            elif equals(value, "NO"):
                return {
                    "unidad_imss_opciones": None,
                    "unidad_imss": None,
                }
        else:
            if value.isdecimal():
                val = int(value)
                if 0 < val <= len(unidades):
                    return {"unidad_imss": cat[unidades[val - 1]]}
                else:
                    dispatcher.utter_message(template="utter_numero_incorrecto")
            else:
                if equals(value, "OTRA"):
                    return {
                        "unidad_imss_opciones": None,
                        "unidad_imss": None,
                    }
                dispatcher.utter_message(template="utter_numero_incorrecto")

        return {"unidad_imss": None}

    def validate_categoria(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if not isinstance(value, str):
            return {"categoria": None}

        value = remove_style(value)

        cat_solicitudes = quejas.get_cat_solicitudes()
        full_cat = {
            cat.get("orden"): cat.get("categoriaId")
            for cat in cat_solicitudes
        }
        cat = [x.get("nombreCategoria", "") for x in cat_solicitudes]

        if value.isdecimal():
            val = int(value)
            if 0 < val <= len(cat):
                if val != 1: # Medicamentos
                    dispatcher.utter_message(template="utter_escribir_quejas")
                    redis.setex(tracker.sender_id+":message_flag", data_ex, "true")
                    return {
                        "categoria": full_cat[val],
                        "medicamento_opciones": "",
                        "confirmar_medicamento": "",
                    }
                return {
                    "categoria": full_cat[val],
                    "medicamentos": "[]",
                }

        else:
            cat_norm = [strip_accents(c).upper() for c in cat]
            similar = compare.get_most_similar(strip_accents(value).upper(), tuple(cat_norm))
            if similar:
                similar = cat[cat_norm.index(similar)]
                if val != 1: # No Medicamentos
                    dispatcher.utter_message(template="utter_escribir_quejas")
                    redis.setex(tracker.sender_id+":message_flag", data_ex, "true")
                    return {
                        "categoria": full_cat[cat.index(similar) + 1],
                        "medicamento_opciones": "",
                        "confirmar_medicamento": "",
                    }
                return {
                    "categoria": full_cat[cat.index(similar) + 1],
                    "medicamentos": "[]",
                }

        return {"categoria": None}

    def validate_medicamento_opciones(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if not isinstance(value, str):
            return {"medicamento_opciones": None}

        value = remove_style(value)

        medicamentos = json.loads(tracker.get_slot("medicamentos"))
        if equals(value, "CONTINUAR"):
            if len(medicamentos) > 0:
                dispatcher.utter_message(template="utter_escribir_quejas")
                redis.setex(tracker.sender_id+":message_flag", data_ex, "true")
                return {
                    "medicamento_opciones": "",
                    "confirmar_medicamento": True,
                }
            else:
                dispatcher.utter_message(template="utter_agregar_un_medicamento")
                return {"medicamento_opciones": None}

        if len(value) < 5:
            dispatcher.utter_message(template="utter_medicamento_no_informacion")
            return {"medicamento_opciones": None}

        cat = [
            cat.get("nombreMedicamento", "")
            for cat in quejas.get_cat_medicamentos()
        ]
        cat_norm = [strip_accents(c).upper() for c in cat]

        coincidences = compare.get_coincidences(strip_accents(value).upper(), tuple(cat_norm))
        if len(coincidences) == 0:
            dispatcher.utter_message(template="utter_medicamento_no_encontrado")
            return {"medicamento_opciones": None}
        elif len(coincidences) > 10:
            dispatcher.utter_message(template="utter_medicamento_no_informacion")
            return {"medicamento_opciones": None}
        elif strip_accents(value).upper() in cat_norm:
            medicamento = cat[cat_norm.index(strip_accents(value).upper())]
            medicamentos.append(medicamento)

            dispatcher.utter_message(
                template="utter_medicamento_registrado",
                medicamento=medicamento)
            return {
                "medicamento_opciones": None,
                "medicamentos": json.dumps(medicamentos),
            }

        coincidences = [cat[cat_norm.index(c)] for c in coincidences]
        return {
            "medicamento_opciones": json.dumps(coincidences),
            "confirmar_medicamento": None,
        }

    def validate_confirmar_medicamento(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        if not isinstance(value, str):
            return {"confirmar_medicamento": None}

        value = remove_style(value)

        meds = json.loads(tracker.get_slot("medicamento_opciones"))
        if len(meds) == 1:
            if equals(value, "SI"):
                medicamentos = json.loads(tracker.get_slot("medicamentos"))
                medicamentos.append(meds[0])
                dispatcher.utter_message(
                    template="utter_medicamento_registrado",
                    medicamento=meds[0])

                return {
                    "confirmar_medicamento": True,
                    "medicamento_opciones": None,
                    "medicamentos": json.dumps(medicamentos),
                }
            elif equals(value, "NO"):
                return {
                    "confirmar_medicamento": False,
                    "medicamento_opciones": None,
                }
        else:
            if value.isdecimal():
                val = int(value)
                if 0 < val <= len(meds):
                    medicamentos = json.loads(tracker.get_slot("medicamentos"))
                    medicamentos.append(meds[val - 1])
                    dispatcher.utter_message(
                        template="utter_medicamento_registrado",
                        medicamento=meds[val - 1])

                    return {
                        "confirmar_medicamento": False,
                        "medicamento_opciones": None,
                        "medicamentos": json.dumps(medicamentos),
                    }
                else:
                    dispatcher.utter_message(template="utter_numero_incorrecto_elige")
            else:
                if equals(value, "NUEVO"):
                    return {
                        "confirmar_medicamento": False,
                        "medicamento_opciones": None,
                    }
                dispatcher.utter_message(template="utter_numero_incorrecto_elige")

        return {"confirmar_medicamento": None}

    def validate_queja(
        self,
        value: Text,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        value = remove_style(value)

        logging.debug("ENVIAR QUEJA")
        # message = str(redis.get(tracker.sender_id+":message"))
        # message_json = json.loads(message)
        message_json = extract_metadata_from_tracker(tracker)
        logging.debug(message_json)

        return_dict = {"queja": None}
        queja_id = tracker.get_slot("queja_id")
        if queja_id is None or queja_id == "":
            queja_template = {
                "status": quejas.status_new,
                "nss": tracker.get_slot("nss")[:10],
                "nombre": tracker.get_slot("nombre"),
                "telefono": tracker.sender_id.replace("rasa:", ""),
                "entidadFederativa": tracker.get_slot("estado"),
                "municipio": tracker.get_slot("ciudad"),
                "categoriaId": tracker.get_slot("categoria"),
                "nombrePaciente": tracker.get_slot("nombre_beneficiario"),
                "diagnostico": tracker.get_slot("diagnostico"),
                "cveClues": tracker.get_slot("unidad_imss"),
            }
            if int(tracker.get_slot("categoria")) == 1:
                cat = {
                    cat.get("nombreMedicamento"): cat.get("medicamentoId")
                    for cat in quejas.get_cat_medicamentos()
                }
                queja_template.update({
                    "medicamentos": list(set([cat[med] for med in json.loads(tracker.get_slot("medicamentos"))]))
                })
            if str(tracker.get_slot("alta_apo")) == "false":
                queja_template.update({
                    "altaApo": 0
                })

            r = quejas.add_queja(queja_template)
            logging.debug(r)
            queja_id = r.get("quejaId")
            queja_version = r.get("version")
            return_dict.update({
                "queja_id": queja_id,
                "queja_version": queja_version
            })
            # Increase quota
            logging.debug("increasing quota for {}".format(tracker.sender_id))
            quota.increase_quota(tracker.sender_id)

        # check if media is voice
        if message_json.get("content_type", "") == "voice":
            try:
                new_file = audio.download_and_convert(message_json.get("media_url"))
                logging.debug(f"downloaded and converted file: {new_file}")
            except e:
                logging.error(e)

        r = quejas.add_mensaje({
            "quejaId": int(queja_id),
            "from": message_json.get("from"),
            "body": message_json.get("body"),
            "mediaUrl": message_json.get("media_url"),
            "contentType": message_json.get("content_type")
        })
        logging.debug(r)
        dispatcher.utter_message(template="utter_mas_quejas")

        return return_dict

    def request_next_slot(
        self,
        dispatcher: "CollectingDispatcher",
        tracker: "Tracker",
        domain: Dict[Text, Any],
    ) -> Optional[List[EventType]]:

        for slot in self.required_slots(tracker):
            if self._should_request_slot(tracker, slot):
                logging.debug(f"Request next slot '{slot}'")
                if (slot == "confirmar_estado" or slot == "ciudad") and str(tracker.get_slot("estado")) == estados.cat_estados[0]:
                    dispatcher.utter_message(
                        template=f"utter_ask_{slot}" + "_df", **tracker.slots)
                elif slot == "categoria":
                    categorias = "\n".join([
                        f"*{cat.get('orden')}.* {cat.get('nombreCategoria')}"
                        for cat in quejas.get_cat_solicitudes()
                    ])
                    dispatcher.utter_message(
                        template=f"utter_ask_{slot}", categorias=categorias, **tracker.slots)
                elif slot == "nombre_beneficiario":
                    beneficiarios = json.loads(tracker.get_slot("nombre_beneficiario_opciones"))
                    if len(beneficiarios) > 1:
                        nombres = [b.get("nombre") for b in beneficiarios]
                        apellidos_paternos = [b.get("primerApellido") for b in beneficiarios]
                        apellidos_maternos = [b.get("segundoApellido") for b in beneficiarios]

                        nombres = "\n".join([f"*{i+1}.* {nom} {app} {apm}" for i, (nom, app, apm) in enumerate(zip(nombres, apellidos_paternos, apellidos_maternos))])
                        dispatcher.utter_message(
                            template=f"utter_ask_nombre_beneficiario_opciones",
                            nombres=nombres,
                            **tracker.slots)
                    else:
                        dispatcher.utter_message(
                            template=f"utter_ask_{slot}", **tracker.slots)
                elif slot == "queja_id":
                    categorias = {
                        cat.get("categoriaId"): cat.get("nombreCategoria")
                        for cat in quejas.get_cat_solicitudes()
                    }
                    solicitudes = json.loads(tracker.get_slot("solicitud_opciones"))
                    categorias_fechas = [
                        f"*{i+1}.* {categorias[s.get('categoriaId', 1)]} _{momentum.momentum(s.get('fechaHora', ''))}_"
                        for i, s in enumerate(solicitudes)]
                    categorias_fechas += ["*N.* Nueva solicitud"]
                    numero = len(solicitudes)
                    dispatcher.utter_message(
                        template=f"utter_ask_{slot}",
                        numero=numero,
                        solicitudes="\n".join(categorias_fechas),
                        **tracker.slots)
                elif slot == "medicamento_opciones":
                    meds = json.loads(tracker.get_slot("medicamentos"))
                    if not meds:
                        dispatcher.utter_message(template="utter_ask_medicamento_opciones")
                    else:
                        dispatcher.utter_message(template="utter_ask_medicamento_opciones_o_continuar")
                elif slot == "confirmar_medicamento":
                    meds = json.loads(tracker.get_slot("medicamento_opciones"))
                    if len(meds) == 1:
                        dispatcher.utter_message(
                            template="utter_ask_confirmar_medicamento_similar",
                            medicamento=meds[0])
                    else:
                        dispatcher.utter_message(
                            template=f"utter_ask_confirmar_medicamento_opciones",
                            opciones_medicamentos="\n".join(
                                [f"*{i+1}*. {m}" for i, m in enumerate(meds)]
                            ))
                elif slot == "unidad_imss":
                    opciones_json = json.loads(tracker.get_slot("unidad_imss_opciones"))
                    if len(opciones_json) > 1:
                        opciones = [f"*{i+1}*. {e}" for i, e in enumerate(opciones_json)]
                        dispatcher.utter_message(
                            template=f"utter_ask_unidad_imss", opciones="\n".join(opciones))
                    else:
                        dispatcher.utter_message(
                            template=f"utter_ask_unidad_imss_confirmar", unidad=opciones_json[0])
                else:
                    dispatcher.utter_message(
                        template=f"utter_ask_{slot}", **tracker.slots)
                return [SlotSet("requested_slot", slot)]

        # no more required slots to fill
        return None


class ActionEnd(Action):
    def name(self) -> Text:
        return "action_end"

    def run(self,
            dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        queja_id = tracker.get_slot("queja_id")
        queja_version = tracker.get_slot("queja_version")
        if queja_id is not None and queja_id != "":
            if queja_version is not None:
                u = quejas.update_queja(queja_id,
                                        status=quejas.status_pending,
                                        version=queja_version)
                logging.debug(u)
                dispatcher.utter_message(template="utter_end")
            else:
                dispatcher.utter_message(template="utter_end_mensajes")
        else:
            dispatcher.utter_message(template="utter_end_no_queja")
        redis.delete(tracker.sender_id + ":message_flag")
        return [AllSlotsReset(), Restarted()]
