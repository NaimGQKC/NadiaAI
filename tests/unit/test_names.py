"""Tests for person-name validation (utils/names.py)."""

from nadia_ai.utils.names import clean_name_list, is_valid_person_name, strip_honorific


class TestStripHonorific:
    def test_removes_leading_titles(self):
        assert strip_honorific("Doña Carmen Alejandre Sabio") == "Carmen Alejandre Sabio"
        assert strip_honorific("Don José Javier Gil") == "José Javier Gil"
        assert strip_honorific("D. Florentino Gabás") == "Florentino Gabás"
        assert strip_honorific("Dña. María Pérez") == "María Pérez"
        assert strip_honorific("Sra. Ana López Ruiz") == "Ana López Ruiz"

    def test_idempotent_and_safe(self):
        assert strip_honorific("Carmen Alejandre Sabio") == "Carmen Alejandre Sabio"
        assert strip_honorific(strip_honorific("Doña Carmen Sabio")) == "Carmen Sabio"
        assert strip_honorific("") == ""
        assert strip_honorific(None) == ""


class TestIsValidPersonName:
    def test_accepts_real_spanish_names(self):
        for name in [
            "Jose Manuel Puente Sanclemente",
            "María del Sol Fresneda",
            "Pepita Segalà Saperas",  # Catalan accent
            "Abdelkader Ben El Mokhtar",
            "Josefa Serrano Canser",
            "Bartolomé Muelas Muelas",
        ]:
            assert is_valid_person_name(name), name

    def test_rejects_legal_boilerplate(self):
        for phrase in [
            "En Cualquier Caso",
            "Cuyo Intento De Notificación Resulte Infructuoso",
            "Desconocidos O De Ignorado Domicilio Y",
            "Según Lo Previsto Por El Art",
            "Se Declararán Abandonadas A Favor Del Estado Y Prescritas Las Citadas Consignaciones",
        ]:
            assert not is_valid_person_name(phrase), phrase

    def test_rejects_llm_placeholders(self):
        for phrase in [
            "Not specified in the text.",
            "Nombres completos no proporcionados en el texto",
            "Nombres completos desconocidos",
            "Heirs of Benito Artal Artal",
            "NOMBRE COMPLETO DE LOS HEREDEROS (si se menciona)",
            "Herederos de Gheorge Merlan",
        ]:
            assert not is_valid_person_name(phrase), phrase

    def test_rejects_institutions_and_garbage(self):
        for phrase in [
            "Subsecretaría (División de Derechos de Gracia y otros Derechos)",
            "Notaría de María del Sol Fresneda",
            "Juzgado de Primera Instancia",
            "Avelina Ferna 769 Ndez Varela",  # mojibake digits
            "D",
            "",
            None,
        ]:
            assert not is_valid_person_name(phrase), phrase

    def test_rejects_single_word(self):
        assert not is_valid_person_name("Zaragoza")

    def test_rejects_relationship_phrases(self):
        # Notarial titles often yield a kinship descriptor, not a name.
        for phrase in [
            "Hijo De Don Federico",
            "Viuda de Antonio Pérez",
            "Esposa Del Causante",
            "Hermanos Garcia Lopez",
        ]:
            assert not is_valid_person_name(phrase), phrase

    def test_still_accepts_honorific_prefixed_name(self):
        # "Don"/"Doña" are honorifics, not kinship words — keep these valid.
        assert is_valid_person_name("Don Iván Raúl Cabrera Cerpa")
        assert is_valid_person_name("Doña Silvia Puente Gil")


class TestCleanNameList:
    def test_filters_and_dedupes(self):
        names = [
            "Jaime Garcia Añoveros",
            "Si En El Plazo De Un Mes A Contar Desde La Publicación",
            "jaime garcia añoveros",  # dup, different case
            "Francisco Aviles",
            "Not specified in the text.",
        ]
        cleaned = clean_name_list(names)
        assert cleaned == ["Jaime Garcia Añoveros", "Francisco Aviles"]

    def test_handles_empty_and_non_strings(self):
        assert clean_name_list(None) == []
        assert clean_name_list([]) == []
        assert clean_name_list([None, 42, ""]) == []
