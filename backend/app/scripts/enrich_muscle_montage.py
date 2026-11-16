"""Inject authoritative IONM electrode-montage detail into the muscle seed.

This is a one-shot, idempotent curation tool. It writes a structured
``electrode_montage`` object onto every entry of ``muscles`` inside the JSON
seed (``backend/app/seed_data/ionm_seed.json``) so that each recording target
carries precise, clinically real placement data: active (G1) and reference (G2)
electrode positions, recording technique, innervating nerve, myotome / cranial
nerve, ground guidance and a practical monitoring pearl.

The montage values below are genuine intraoperative neuromonitoring practice
(belly-tendon needle pairs for limb/trunk EMG and tcMEP, sub-dermal pairs for
facial and extra-ocular cranial muscles, NIM endotracheal surface electrodes for
the vocalis, perianal needle pairs for the external anal sphincter, and a click
stimulus + Cz-Ai recording chain for auditory brainstem responses).

Run locally after editing the montage map:

    .\\.venv\\Scripts\\python.exe -m backend.app.scripts.enrich_muscle_montage

It only adds / refreshes the ``electrode_montage`` field; it never edits
coordinates, names, domains or any other curated content.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.core.config import settings


# Recording technique constants (Greek-language UI strings).
_NEEDLE_PAIR = "Υποδόριο ζεύγος βελονών (belly-tendon)"
_NEEDLE_PAIR_DEEP = "Ζεύγος βελονών εν τω βάθει στη μυϊκή γαστέρα"
_SUBDERMAL = "Υποδόριο ζεύγος βελονών"
_INTRAMUSCULAR = "Ενδομυϊκό ζεύγος βελονών"
_NIM_ETT = "Επιφανειακά ηλεκτρόδια ενδοτραχειακού σωλήνα NIM"
_PERIANAL = "Ζεύγος βελονών περιπρωκτικά"
_CLICK = "Ακουστικός ερεθισμός κλικ + φλοιική/υποφλοιική καταγραφή"
_GROUND_LIMB = "Γείωση μεταξύ σημείου διέγερσης και καταγραφής στο ίδιο άκρο"
_GROUND_HEAD = "Γείωση στο μέτωπο (Fpz) ή στον ώμο"


def _emg(
    active: str,
    reference: str,
    nerve: str,
    myotome: str,
    pearl: str,
    *,
    technique: str = _NEEDLE_PAIR,
    inter_electrode_cm: float = 2.0,
    ground: str = _GROUND_LIMB,
) -> dict[str, Any]:
    return {
        "technique": technique,
        "active": f"Ενεργό (G1): {active}",
        "reference": f"Αναφορά (G2): {reference}",
        "inter_electrode_cm": inter_electrode_cm,
        "nerve": nerve,
        "myotome": myotome,
        "ground": ground,
        "pearl": pearl,
    }


def _cranial(
    active: str,
    reference: str,
    nerve: str,
    branch: str,
    pearl: str,
    *,
    technique: str = _SUBDERMAL,
    inter_electrode_cm: float = 1.0,
) -> dict[str, Any]:
    return {
        "technique": technique,
        "active": f"Ενεργό (G1): {active}",
        "reference": f"Αναφορά (G2): {reference}",
        "inter_electrode_cm": inter_electrode_cm,
        "nerve": nerve,
        "branch": branch,
        "ground": _GROUND_HEAD,
        "pearl": pearl,
    }


# Authoritative montage map keyed by Latin muscle name.
MONTAGE: dict[str, dict[str, Any]] = {
    # ---- Upper limb (cervical myotomes) ----
    "Deltoid": _emg(
        "μέσο δελτοειδές, 5 cm κάτω από το ακρώμιο",
        "δελτοειδές φύμα βραχιονίου (κατάφυση)",
        "Μασχαλιαίο νεύρο",
        "C5–C6",
        "Κύριος δείκτης C5· έλεγχος απαγωγής ώμου σε αυχενική αποσυμπίεση.",
        inter_electrode_cm=3.0,
    ),
    "Biceps brachii": _emg(
        "πρόσθια γαστέρα μέσου βραχιονίου",
        "τένοντας δικεφάλου στον αγκώνα",
        "Μυοδερματικό νεύρο",
        "C5–C6",
        "Δείκτης C6· κάμψη αγκώνα.",
        inter_electrode_cm=3.0,
    ),
    "Brachioradialis": _emg(
        "έξω επιφάνεια αντιβραχίου, άνω 1/3",
        "κερκιδική στυλοειδής απόφυση (περιφερικά)",
        "Κερκιδικό νεύρο",
        "C5–C6",
        "Χρήσιμος C6 δείκτης μαζί με δικέφαλο.",
        inter_electrode_cm=3.0,
    ),
    "Triceps brachii": _emg(
        "οπίσθιο βραχιόνιο, μακρά κεφαλή",
        "ωλέκρανο (κατάφυση)",
        "Κερκιδικό νεύρο",
        "C6–C8 (κυρίως C7)",
        "Δείκτης C7· έκταση αγκώνα.",
        inter_electrode_cm=3.0,
    ),
    "Extensor carpi radialis": _emg(
        "ραχιαία-έξω επιφάνεια αντιβραχίου, άνω 1/3",
        "ραχιαία βάση 2ου/3ου μετακαρπίου",
        "Κερκιδικό νεύρο",
        "C6–C7",
        "Έκταση καρπού· δείκτης C6–C7.",
        inter_electrode_cm=3.0,
    ),
    "Extensor digitorum": _emg(
        "ραχιαία επιφάνεια αντιβραχίου, μέσο τμήμα",
        "ραχιαία πλευρά καρπού",
        "Οπίσθιο μεσόστεο (κερκιδικό)",
        "C7–C8",
        "Έκταση δακτύλων· δείκτης C7.",
        inter_electrode_cm=3.0,
    ),
    "Extensor carpi ulnaris": _emg(
        "ραχιαία-ωλένια επιφάνεια αντιβραχίου",
        "ραχιαία βάση 5ου μετακαρπίου",
        "Οπίσθιο μεσόστεο (κερκιδικό)",
        "C7–C8",
        "Έκταση/ωλένια απόκλιση καρπού.",
        inter_electrode_cm=3.0,
    ),
    "Flexor carpi radialis": _emg(
        "πρόσθια-έξω επιφάνεια αντιβραχίου, άνω 1/3",
        "παλαμιαία βάση 2ου μετακαρπίου",
        "Μέσο νεύρο",
        "C6–C7",
        "Κάμψη καρπού· δείκτης C6–C7.",
        inter_electrode_cm=3.0,
    ),
    "Flexor carpi ulnaris": _emg(
        "πρόσθια-ωλένια επιφάνεια αντιβραχίου",
        "πισοειδές οστό (κατάφυση)",
        "Ωλένιο νεύρο",
        "C7–T1",
        "Κάμψη/ωλένια απόκλιση καρπού.",
        inter_electrode_cm=3.0,
    ),
    "Flexor digitorum profundus": _emg(
        "πρόσθια αντιβραχίου, εν τω βάθει, μέσο τμήμα",
        "παλαμιαία πλευρά καρπού",
        "Μέσο (πρόσθιο μεσόστεο) / ωλένιο",
        "C8–T1",
        "Κάμψη απώτερων φαλάγγων· δείκτης C8.",
        technique=_NEEDLE_PAIR_DEEP,
        inter_electrode_cm=3.0,
    ),
    "Abductor pollicis brevis": _emg(
        "γαστέρα θέναρ (κύρτωμα αντίχειρα)",
        "1η μετακαρποφαλαγγική άρθρωση αντίχειρα",
        "Μέσο νεύρο",
        "C8–T1",
        "Θέναρ· κύριος δείκτης μέσου νεύρου / C8–T1.",
    ),
    "Abductor digiti minimi": _emg(
        "έσω χείλος παλάμης (υποθέναρ)",
        "5η μετακαρποφαλαγγική άρθρωση",
        "Ωλένιο νεύρο",
        "C8–T1",
        "Υποθέναρ· δείκτης ωλενίου νεύρου / T1.",
    ),
    "First dorsal interosseous": _emg(
        "ραχιαία μεσοδακτύλια πτυχή μεταξύ Ι και ΙΙ μετακαρπίου",
        "ραχιαία 2η μετακαρποφαλαγγική (δείκτης)",
        "Ωλένιο νεύρο",
        "C8–T1",
        "Απαγωγή δείκτη· ευαίσθητος δείκτης T1.",
    ),
    # ---- Trunk / abdominal (thoracic segmental) ----
    "Intercostals": _emg(
        "μεσοπλεύριο διάστημα στο μυοτόμιο-στόχο, μεσομασχαλιαία",
        "παρακείμενο πλευρό",
        "Μεσοπλεύρια νεύρα (θωρακικά)",
        "Τμηματικό Τx",
        "Δύσκολη/σπάνια πρόσβαση· segmental θωρακικό mapping.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=1.5,
    ),
    "Rectus abdominis (upper)": _emg(
        "άνω 1/3 ορθού κοιλιακού, παράμεσα",
        "2–3 cm περιφερικότερα στην ίδια στήλη",
        "Θωρακοκοιλιακά νεύρα",
        "T6–T8",
        "Segmental θωρακικός δείκτης T6–T8.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=2.0,
    ),
    "Rectus abdominis (mid)": _emg(
        "μέσο 1/3 ορθού κοιλιακού, παράμεσα",
        "2–3 cm περιφερικότερα στην ίδια στήλη",
        "Θωρακοκοιλιακά νεύρα",
        "T9–T11",
        "Segmental θωρακικός δείκτης T9–T11.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=2.0,
    ),
    "Rectus abdominis (lower)": _emg(
        "κάτω 1/3 ορθού κοιλιακού, παράμεσα",
        "υπερηβική κατάφυση",
        "Θωρακοκοιλιακά / υπολαγόνιο",
        "T12",
        "Segmental θωρακικός δείκτης T12.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=2.0,
    ),
    "Cremaster": _emg(
        "όσχεο (μόνο σε άνδρες)",
        "παρακείμενο όσχεο",
        "Γεννητικομηριαίο νεύρο",
        "L1–L2",
        "Κρεμαστηρικό αντανακλαστικό· δείκτης L1–L2.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=1.0,
    ),
    # ---- Lower limb (lumbosacral myotomes) ----
    "Iliopsoas": _emg(
        "άνω-έσω μηρός, 2–3 cm κάτω από τον βουβωνικό σύνδεσμο (έξω από τη μηριαία αρτηρία)",
        "πρόσθιος μηρός, 3–4 cm περιφερικότερα",
        "Μηριαίο / οσφυϊκό πλέγμα",
        "L1–L3",
        "Προσοχή στο μηριαίο αγγειονευρώδες δεμάτιο· δείκτης L2–L3.",
        technique=_NEEDLE_PAIR_DEEP,
        inter_electrode_cm=3.0,
    ),
    "Adductor longus": _emg(
        "έσω επιφάνεια μηρού, άνω 1/3",
        "έσω μηριαία, 3–4 cm περιφερικότερα",
        "Θυροειδικό νεύρο",
        "L2–L4",
        "Προσαγωγή μηρού· δείκτης θυροειδικού / L2–L4.",
        inter_electrode_cm=3.0,
    ),
    "Vastus medialis": _emg(
        "έσω-πρόσθια επιφάνεια μηρού, ~10 cm άνω της επιγονατίδας",
        "άνω χείλος επιγονατίδας (τένοντας τετρακεφάλου)",
        "Μηριαίο νεύρο",
        "L2–L4",
        "Έκταση γόνατος· δείκτης μηριαίου / L3–L4.",
        inter_electrode_cm=3.0,
    ),
    "Gluteus medius": _emg(
        "άνω-έξω γλουτιαία περιοχή",
        "μείζων τροχαντήρας (κατάφυση)",
        "Άνω γλουτιαίο νεύρο",
        "L4–S1",
        "Απαγωγή ισχίου· δείκτης άνω γλουτιαίου / L5–S1.",
        inter_electrode_cm=3.0,
    ),
    "Tibialis anterior": _emg(
        "έξω-πρόσθια επιφάνεια κνήμης, άνω 1/3 (έξω από την κνημιαία ακρολοφία)",
        "πρόσθια ποδοκνημική (τένοντας)",
        "Εν τω βάθει περονιαίο",
        "L4–L5",
        "Ραχιαία κάμψη· δημοφιλέστερος δείκτης L4–L5.",
        inter_electrode_cm=3.0,
    ),
    "Extensor hallucis longus": _emg(
        "πρόσθια κνήμη, μέσος-κάτω άξονας",
        "ραχιαία 1η μετατασιοφαλαγγική",
        "Εν τω βάθει περονιαίο",
        "L5–S1 (κυρίως L5)",
        "Έκταση μεγάλου δακτύλου· καθαρός δείκτης L5.",
        inter_electrode_cm=2.0,
    ),
    "Peroneus longus": _emg(
        "έξω επιφάνεια κνήμης, άνω 1/3",
        "έξω σφυρό (περιφερικά)",
        "Επιπολής περονιαίο",
        "L5–S1",
        "Πρηνισμός/πελματιαία κάμψη· δείκτης L5–S1.",
        inter_electrode_cm=3.0,
    ),
    "Biceps femoris": _emg(
        "οπίσθια-έξω επιφάνεια μηρού",
        "κεφαλή περόνης (περιφερικός τένοντας)",
        "Ισχιακό (περονιαία μοίρα)",
        "L5–S2",
        "Κάμψη γόνατος· δείκτης S1.",
        inter_electrode_cm=3.0,
    ),
    "Gastrocnemius medial": _emg(
        "έσω κεφαλή, άνω 1/3 οπίσθιας κνήμης",
        "Αχίλλειος τένοντας",
        "Κνημιαίο νεύρο",
        "S1–S2",
        "Πελματιαία κάμψη· δείκτης S1.",
        inter_electrode_cm=3.0,
    ),
    "Abductor hallucis": _emg(
        "έσω επιφάνεια πέλματος (γαστέρα)",
        "1η μετατασιοφαλαγγική άρθρωση",
        "Έσω πελματιαίο (κνημιαίο)",
        "S1–S2",
        "Δείκτης κάτω ιερών ριζών / conus-cauda.",
    ),
    "External anal sphincter": _emg(
        "περιπρωκτικός μυς, θέση ώρας 3",
        "περιπρωκτικός μυς, θέση ώρας 9",
        "Αιδοιικό νεύρο",
        "S2–S4",
        "Υποχρεωτικός σε conus/cauda & χαμηλές επεμβάσεις· παρακολούθηση αιδοιικού.",
        technique=_PERIANAL,
        inter_electrode_cm=1.5,
    ),
    # ---- Cranial: facial nerve (CN VII) territory ----
    "Frontalis": _cranial(
        "μέτωπο, υπερόφρυο τόξο",
        "2 cm πλάγια στο μέτωπο",
        "CN VII",
        "Κροταφικός κλάδος",
        "Άνω προσωπικό· κροταφικός κλάδος VII.",
    ),
    "Orbicularis oculi": _cranial(
        "έξω κανθός, κάτω βλέφαρο",
        "ζυγωματικό τόξο",
        "CN VII",
        "Ζυγωματικός κλάδος",
        "Σύγκλειση βλεφάρων· ζυγωματικός κλάδος VII.",
    ),
    "Orbicularis oris": _cranial(
        "άνω χείλος, παράμεσα",
        "ρινοχειλική αύλακα",
        "CN VII",
        "Παρειακός κλάδος",
        "Στόμα· παρειακός/χειλικός κλάδος VII.",
    ),
    "Mentalis": _cranial(
        "πώγων",
        "κάτω γνάθος, παράμεσα",
        "CN VII",
        "Γναθιαίος (χειλικός) κλάδος",
        "Κάτω χείλος/πώγων· γναθιαίος κλάδος VII.",
    ),
    "Platysma": _cranial(
        "πρόσθιος τράχηλος, υποπλατυσμικά",
        "κλείδα",
        "CN VII",
        "Τραχηλικός κλάδος",
        "Τραχηλικός κλάδος VII.",
    ),
    # ---- Cranial: trigeminal motor (CN V3) ----
    "Masseter": _cranial(
        "μασητήρας, πάνω από τη γωνία της γνάθου",
        "ζυγωματικό τόξο",
        "CN V (κινητική)",
        "Κάτω γναθικό (V3)",
        "Σύγκλειση γνάθου· κινητικό τρίδυμο.",
        technique=_INTRAMUSCULAR,
    ),
    "Temporalis": _cranial(
        "κροταφικός βόθρος",
        "κροταφική γραμμή",
        "CN V (κινητική)",
        "Κάτω γναθικό (V3)",
        "Σύγκλειση γνάθου· κινητικό τρίδυμο.",
        technique=_INTRAMUSCULAR,
    ),
    # ---- Cranial: extra-ocular muscles ----
    "Medial rectus": _cranial(
        "έσω ορθός (ενδοκογχικά, από οφθαλμίατρο)",
        "έξω κανθός",
        "CN III",
        "Κοινό κινητικό",
        "Προσαγωγή οφθαλμού· τοποθέτηση από οφθαλμίατρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.5,
    ),
    "Inferior rectus": _cranial(
        "κάτω ορθός (ενδοκογχικά)",
        "κάτω κογχικό χείλος",
        "CN III",
        "Κοινό κινητικό",
        "Κάτω βλέμμα· κοινό κινητικό νεύρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.5,
    ),
    "Superior oblique": _cranial(
        "άνω λοξός (ενδοκογχικά, άνω-έσω)",
        "άνω-έσω κογχικό χείλος",
        "CN IV",
        "Τροχιλιακό",
        "Κάτω-έσω στροφή· τροχιλιακό νεύρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.5,
    ),
    "Lateral rectus": _cranial(
        "έξω ορθός (ενδοκογχικά, έξω)",
        "έξω κανθός",
        "CN VI",
        "Απαγωγό",
        "Απαγωγή οφθαλμού· απαγωγό νεύρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.5,
    ),
    # ---- Cranial: lower (IX, X, XI, XII) ----
    "Stylopharyngeus": _cranial(
        "στυλοφαρυγγικός (διαφαρυγγική προσπέλαση)",
        "παρακείμενο φαρυγγικό τοίχωμα",
        "CN IX",
        "Γλωσσοφαρυγγικό",
        "Δύσκολη πρόσβαση· ανύψωση φάρυγγα (IX).",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.5,
    ),
    "Soft palate": _cranial(
        "μαλακή υπερώα, παράμεσα",
        "σκληρή-μαλακή υπερώα όριο",
        "CN IX / X",
        "Φαρυγγικό πλέγμα",
        "Ανύψωση υπερώας· συνεκτίμηση με X.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.5,
    ),
    "Cricothyroid": _cranial(
        "κρικοθυρεοειδής μυς (πρόσθιος τράχηλος)",
        "θυρεοειδικός χόνδρος",
        "CN X",
        "Έξω κλάδος άνω λαρυγγικού (eSLN)",
        "Τάση φωνητικών χορδών· eSLN.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.7,
    ),
    "Vocalis (via NIM ETT)": {
        "technique": _NIM_ETT,
        "active": "Ενεργό: επιφανειακές επαφές NIM ETT σε επαφή με τις φωνητικές χορδές",
        "reference": "Αναφορά: δεύτερη επαφή του σωλήνα (bipolar)",
        "nerve": "CN X",
        "branch": "Παλίνδρομο λαρυγγικό (RLN)",
        "ground": "Γείωση στο στέρνο",
        "stimulation": "Διέγερση 1–2 mA στο νεύρο/πεδίο",
        "pearl": "Σωστή στροφή σωλήνα στη γλωττίδα· παρακολούθηση RLN σε θυρεοειδεκτομή.",
    },
    "Genioglossus": _cranial(
        "σώμα γλώσσας, πλάγια (αποφυγή μέσης γραμμής)",
        "ραχιαία γλώσσα",
        "CN XII",
        "Υπογλώσσιο",
        "Προβολή γλώσσας· υπογλώσσιο νεύρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=0.7,
    ),
    "Sternocleidomastoid": _cranial(
        "μέσο τμήμα ΣΚΜ",
        "στερνική/κλειδική κατάφυση",
        "CN XI",
        "Παραπληρωματικό (+ C2–C3)",
        "Στροφή κεφαλής· παραπληρωματικό νεύρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=2.0,
    ),
    "Trapezius (upper)": _cranial(
        "άνω τραπεζοειδής, μέσο τμήμα",
        "ακρώμιο/άκανθα ωμοπλάτης",
        "CN XI",
        "Παραπληρωματικό (+ C3–C4)",
        "Ανύψωση ώμου· παραπληρωματικό νεύρο.",
        technique=_INTRAMUSCULAR,
        inter_electrode_cm=2.0,
    ),
    # ---- Auditory brainstem responses (not EMG) ----
    "Auditory brainstem responses": {
        "technique": _CLICK,
        "stimulus": "Click 90 dB nHL, εναλλασσόμενη πολικότητα, ~11.1/s, αντίπλευρη συγκάλυψη 40 dB",
        "active": "Ενεργό: Cz (κορυφή)",
        "reference": "Αναφορά: ομόπλευρο A1/A2 (μαστοειδής/λοβός ωτός)",
        "ground": "Γείωση: Fpz",
        "nerve": "CN VIII",
        "averages": "1000–2000 sweeps",
        "pearl": "Παρακολούθηση κύματος V (latency ↑ / amplitude ↓ = προειδοποίηση).",
    },
}


def load_seed() -> dict[str, Any]:
    with settings.seed_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def enrich(seed: dict[str, Any]) -> tuple[int, list[str]]:
    muscles = seed.get("muscles", [])
    updated = 0
    missing: list[str] = []
    for muscle in muscles:
        lat_name = muscle.get("lat_name")
        montage = MONTAGE.get(lat_name)
        if not montage:
            missing.append(lat_name or "<unnamed>")
            continue
        if muscle.get("electrode_montage") != montage:
            muscle["electrode_montage"] = montage
            updated += 1
    return updated, missing


def main() -> None:
    seed = load_seed()
    updated, missing = enrich(seed)
    with settings.seed_path.open("w", encoding="utf-8") as file:
        json.dump(seed, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"muscles enriched with electrode_montage: {updated}")
    if missing:
        print(f"muscles without a montage entry ({len(missing)}): {', '.join(sorted(missing))}")
    else:
        print("every muscle now carries an authoritative electrode_montage")


if __name__ == "__main__":
    main()
