import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const rootDir = process.cwd();
const htmlPath = path.join(rootDir, "ionm-reference.html");
const coordsPath = path.join(rootDir, "ionm-coords.json");
const outputPath = path.join(rootDir, "backend", "app", "seed_data", "ionm_seed.json");

const html = fs.readFileSync(htmlPath, "utf8");
const coordinates = JSON.parse(fs.readFileSync(coordsPath, "utf8"));

const names = [
  "ROOTS_LUMBAR",
  "ROOTS_CERVICAL",
  "ROOTS_THORACIC",
  "DISCS_LUMBAR",
  "DISCS_CERVICAL",
  "DISCS_THORACIC",
  "SSEP_DATA",
  "PEDICLE_ROOTS",
  "CRANIAL_NERVES",
  "ENT_TEMPLATES",
];

const extracted = Object.fromEntries(names.map((name) => [name, evaluateConst(name)]));

const regionLabels = {
  cervical: "Αυχενική",
  thoracic: "Θωρακική",
  lumbar: "Οσφυϊκή",
};

const regionRoots = {
  cervical: extracted.ROOTS_CERVICAL,
  thoracic: extracted.ROOTS_THORACIC,
  lumbar: extracted.ROOTS_LUMBAR,
};

const regionDiscs = {
  cervical: extracted.DISCS_CERVICAL,
  thoracic: extracted.DISCS_THORACIC,
  lumbar: extracted.DISCS_LUMBAR,
};

const protocolDocs = [
  {
    key: "protocol.spine.mep_emg.setup",
    title_el: "Spine MEP/EMG setup and response",
    domain: "spine",
    default_for_domains: ["spine"],
    sort_order: 10,
    content: {
      scope: "Root/myotome-driven spine IONM plan derived from the selected disc or nerve-root level.",
      channel_selection: "Select recording muscles from linked root myotomes and verify bilateral placement when mapped coordinates exist.",
      baseline: "Record baseline after positioning and before the first high-risk surgical step.",
      technical_checks: "Before clinical escalation, check electrodes, stimulation/recording setup, pressure/perfusion, temperature and anesthesia context.",
      anesthesia: "Avoid neuromuscular blockade for EMG/MEP interpretation; verify TOF/clinical context before accepting muscle responses.",
    },
    source: "ionm-reference.html",
  },
  {
    key: "protocol.spine.mep.warning_criteria",
    title_el: "Spine MEP warning criteria and differential",
    domain: "spine",
    default_for_domains: ["spine"],
    sort_order: 20,
    content: {
      warning: "Significant MEP amplitude loss or complete loss requires repeat acquisition and structured differential before escalation.",
      differential: "Check anesthetic change, neuromuscular blockade, stimulation output, recording electrodes, blood pressure, temperature and surgical traction/compression timing.",
      action: "Repeat stimulation, compare same-channel baseline and bilateral pattern, then communicate graded concern with timing and affected myotomes.",
      anesthesia: "TIVA-based technique is preferred for stable MEP; avoid long-acting neuromuscular blockade when MEP/EMG interpretation is required.",
      bibliography_query: "motor evoked potentials warning criteria intraoperative neuromonitoring spine anesthesia",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.spine.free_running_emg.troubleshooting",
    title_el: "Free-running EMG artifact and irritation response",
    domain: "spine",
    default_for_domains: ["spine"],
    sort_order: 30,
    content: {
      scope: "Free-running EMG is interpreted as a context-dependent irritation/technical signal, not a standalone injury diagnosis.",
      artifact_checks: "Differentiate burst/trains from cautery, irrigation, cable movement, poor electrode contact and ambient electrical noise.",
      response: "Correlate train activity with surgical manipulation, root level, laterality and concurrent MEP/SSEP stability.",
      documentation: "Record timing, involved channels, surgical step, technical corrections and whether activity resolved.",
      bibliography_query: "free running EMG trains intraoperative neuromonitoring spine artifact",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.cranial.emg.setup",
    title_el: "Cranial nerve EMG setup",
    domain: "cranial",
    default_for_domains: ["cranial"],
    sort_order: 10,
    content: {
      scope: "Procedure-template or single-nerve cranial EMG plan derived from the selected CN set.",
      channel_selection: "Map each selected cranial nerve to its recording muscle and confirm laterality before baseline.",
      setup: "Verify electrode contact and stable low-noise baseline before dissection around the nerve at risk.",
      response: "When signal quality changes, check electrode position and technical factors before surgical interpretation.",
    },
    source: "ionm-reference.html",
  },
  {
    key: "protocol.cranial.baep.setup",
    title_el: "BAEP cranial nerve VIII monitoring setup",
    domain: "cranial",
    default_for_domains: ["cranial"],
    sort_order: 20,
    content: {
      scope: "Brainstem auditory evoked potentials support CN VIII/brainstem risk monitoring when clinically indicated.",
      stimulation: "Use insert earphones/click stimulation with masking as appropriate and document baseline wave morphology.",
      warning: "Track wave V latency and amplitude trends; confirm reproducibility and exclude technical or anesthetic/temperature factors.",
      response: "Escalate persistent reproducible wave V deterioration with timing and surgical correlation.",
      bibliography_query: "brainstem auditory evoked potentials intraoperative monitoring acoustic neuroma warning criteria",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.cranial.cn7.emg",
    title_el: "Facial nerve EMG monitoring response",
    domain: "cranial",
    default_for_domains: ["cranial"],
    sort_order: 30,
    content: {
      setup: "Record from orbicularis oculi and orbicularis oris when facial nerve risk is present, with side-specific labeling.",
      stimulation: "Use direct stimulation mapping thresholds in surgical context and avoid interpreting artifact as nerve activation.",
      warning: "Sustained neurotonic discharges or loss of stimulation response require technical verification and immediate surgical correlation.",
      anesthesia: "Avoid neuromuscular blockade after intubation dose when facial EMG interpretation is required.",
      bibliography_query: "facial nerve EMG intraoperative monitoring neurotonic discharges",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.ssep.warning_criteria",
    title_el: "SSEP warning criteria",
    domain: "ssep",
    default_for_domains: ["ssep"],
    sort_order: 10,
    content: {
      amplitude: "50% πτώση πλάτους από baseline",
      latency: "10% αύξηση λανθάνουσας από baseline",
      complete_loss: "Πλήρης απώλεια κύματος απαιτεί άμεση ειδοποίηση χειρουργού",
      differential: "Πρώτα έλεγχος ηλεκτροδίων, πίεσης, θερμοκρασίας και αναισθησίας",
      anesthesia: "Volatile ≤ 0.5 MAC, ιδανικά TIVA· αποφυγή N₂O· NMB αποδεκτό για SSEP αλλά όχι για EMG/MEP",
    },
    source: "ionm-reference.html",
  },
  {
    key: "protocol.ssep.upper_lower.setup",
    title_el: "Upper/lower limb SSEP setup and differential",
    domain: "ssep",
    default_for_domains: ["ssep"],
    sort_order: 20,
    content: {
      stimulation: "Confirm peripheral stimulation site, current, pulse width and visible/motor response appropriate to the nerve.",
      recording: "Confirm peripheral, subcortical and cortical channels when available to localize peripheral versus central change.",
      warning: "Interpret amplitude and latency trends together and confirm reproducibility before declaring a true alert.",
      differential: "Check limb temperature, perfusion, blood pressure, anesthetic depth, electrode impedance and stimulation displacement.",
      bibliography_query: "somatosensory evoked potentials warning criteria anesthesia temperature perfusion intraoperative monitoring",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.pedicle.thresholds",
    title_el: "Pedicle screw t-EMG thresholds",
    domain: "pedicle",
    default_for_domains: ["pedicle"],
    sort_order: 10,
    content: {
      safe: "> 10 mA: άθικτος αυχένας",
      warning: "5-10 mA: αμφίβολο, πιθανή λέπτυνση φλοιού",
      danger: "< 5 mA: πιθανή ρήξη φλοιού",
      action: "Επανεκτίμηση track, ψηλάφηση ή επανατοποθέτηση ανά context",
      stimulation: "Μονοπολικό probe στο screw shaft ή pilot hole, 0.2-0.3 ms duration, αύξηση από 0 mA",
      prerequisites: "TOF 4/4 πριν τη μέτρηση· επανάληψη 2-3 φορές και καταγραφή από αμφότερα μυικά κανάλια ρίζας",
      pitfalls: "NMB false high thresholds· αίμα/ορός γύρω από βίδα false low· οστεοπόρωση χαμηλότερα thresholds",
    },
    source: "ionm-reference.html",
  },
  {
    key: "protocol.pedicle.cervical.thresholds",
    title_el: "Cervical pedicle/lateral mass stimulation cautions",
    domain: "pedicle",
    default_for_domains: ["pedicle"],
    sort_order: 20,
    content: {
      scope: "Cervical stimulation thresholds require level-specific root coverage and careful interpretation in small bony anatomy.",
      channels: "Record from myotomes adjacent to the stimulated level and document laterality before testing.",
      pitfalls: "Anesthesia/NMB, wet field shunting, poor probe contact and anatomy can alter thresholds.",
      action: "Low or inconsistent threshold should prompt repeat testing, probe contact check and surgical/anatomic reassessment.",
      bibliography_query: "cervical pedicle screw triggered EMG threshold intraoperative neuromonitoring",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.thyroid.rln",
    title_el: "RLN thyroid monitoring",
    domain: "thyroid",
    default_for_domains: ["thyroid"],
    default_for_node_types: ["domain"],
    sort_order: 10,
    content: {
      setup: "NIM ETT με surface electrodes ανάμεσα στις φωνητικές χορδές",
      stim: "1.0 mA, response 100-800 μV",
      latency: "3.5-7.5 ms",
      sequence: "V1 -> R1 -> R2 -> V2",
      los: "Πτώση πλάτους >50% και/ή λανθάνουσα >10%",
    },
    source: "ionm-reference.html",
  },
  {
    key: "protocol.thyroid.los.troubleshooting",
    title_el: "Thyroid loss-of-signal troubleshooting",
    domain: "thyroid",
    default_for_domains: ["thyroid"],
    default_for_node_types: ["domain", "thyroid_protocol"],
    sort_order: 30,
    content: {
      definition: "Loss of signal requires confirmation that the ETT electrode position, stimulator, vagus/RLN stimulation and recording chain are functioning.",
      sequence: "Check equipment, tube position, stimulation current/contact, contralateral/vagus response and surgical event timing.",
      differential: "Separate true neural LOS from tube rotation, secretions, NMB, poor contact or stimulation failure.",
      documentation: "Record V1/R1/R2/V2 sequence, amplitude/latency, corrective steps and whether signal recovered.",
      bibliography_query: "thyroidectomy intraoperative neural monitoring loss of signal troubleshooting RLN",
    },
    source: "curated_clinical_protocol_v1",
  },
  {
    key: "protocol.thyroid.esln",
    title_el: "eSLN thyroid monitoring",
    domain: "thyroid",
    default_for_domains: ["thyroid"],
    default_for_node_types: ["domain"],
    sort_order: 20,
    content: {
      setup: "Επιφανειακά ή βελονοειδή ηλεκτρόδια στον κρικοθυρεοειδή",
      stim: "1.0 mA και ορατό twitch",
      significance: "Τόνος φωνής και υψηλές συχνότητες",
      risk_zone: "Άνω πόλος και Berry's ligament",
    },
    source: "ionm-reference.html",
  },
  {
    key: "protocol.modality.anesthesia.notes",
    title_el: "Anesthesia and physiology notes by modality",
    domain: "cross_modality",
    default_for_domains: ["spine", "cranial", "ssep", "pedicle", "thyroid"],
    sort_order: 90,
    content: {
      mep_emg: "MEP/EMG interpretation is sensitive to neuromuscular blockade and anesthetic changes; verify TOF and technique before escalation.",
      ssep: "SSEP is affected by volatile agents, nitrous oxide, temperature, perfusion and stimulation displacement.",
      cranial_thyroid: "Cranial/thyroid EMG requires avoidance of ongoing NMB and careful electrode/tube position verification.",
      pedicle: "Triggered EMG thresholds require adequate neuromuscular recovery and stable stimulation contact.",
      bibliography_query: "anesthesia effects intraoperative neuromonitoring MEP SSEP EMG neuromuscular blockade",
    },
    source: "curated_clinical_protocol_v1",
  },
];

const seed = {
  generated_from: ["ionm-reference.html", "ionm-coords.json"],
  knowledge_nodes: buildKnowledgeNodes(),
  procedures: buildProcedures(),
  modalities: buildModalities(),
  nerve_roots: buildNerveRoots(),
  spinal_discs: buildSpinalDiscs(),
  cranial_nerves: buildCranialNerves(),
  muscles: buildMuscles(),
  electrode_coordinates: buildCoordinates(),
  ssep_protocols: buildSsepProtocols(),
  pedicle_roots: buildPedicleRoots(),
  protocols: protocolDocs,
  academy_modules: buildAcademyModules(),
};

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, `${JSON.stringify(seed, null, 2)}\n`, "utf8");
console.log(`Wrote ${outputPath}`);
console.log(`knowledge_nodes: ${seed.knowledge_nodes.length}`);
console.log(`muscles: ${seed.muscles.length}`);
console.log(`coordinates: ${seed.electrode_coordinates.length}`);

function evaluateConst(name) {
  const declaration = `const ${name}`;
  const start = html.indexOf(declaration);
  if (start === -1) throw new Error(`Could not find ${name}`);
  const equals = html.indexOf("=", start);
  const literalStart = findLiteralStart(html, equals + 1);
  const literalEnd = findLiteralEnd(html, literalStart);
  const literal = html.slice(literalStart, literalEnd + 1);
  return vm.runInNewContext(`(${literal})`, Object.create(null), { timeout: 1000 });
}

function findLiteralStart(source, offset) {
  for (let index = offset; index < source.length; index += 1) {
    if (source[index] === "{" || source[index] === "[") return index;
  }
  throw new Error("Literal start not found");
}

function findLiteralEnd(source, start) {
  const opener = source[start];
  const closer = opener === "{" ? "}" : "]";
  const stack = [closer];
  let quote = null;
  let escaped = false;
  for (let index = start + 1; index < source.length; index += 1) {
    const char = source[index];
    if (quote) {
      if (escaped) {
        escaped = false;
      } else if (char === "\\") {
        escaped = true;
      } else if (char === quote) {
        quote = null;
      }
      continue;
    }
    if (char === "'" || char === '"' || char === "`") {
      quote = char;
      continue;
    }
    if (char === "{" ) stack.push("}");
    else if (char === "[") stack.push("]");
    else if (char === stack[stack.length - 1]) {
      stack.pop();
      if (stack.length === 0) return index;
    }
  }
  throw new Error("Literal end not found");
}

function buildKnowledgeNodes() {
  const nodes = [];
  addNode(nodes, "ionm", null, "IONM", "root", "platform", ["live", "academy"], 0);
  addNode(nodes, "live", "ionm", "Live", "mode", "live", ["live"], 10);
  addNode(nodes, "academy", "ionm", "Academy", "mode", "academy", ["academy"], 20);

  addNode(nodes, "live.spine", "live", "Σπονδυλική", "domain", "spine", ["live"], 10);
  for (const [region, roots] of Object.entries(regionRoots)) {
    addNode(nodes, `live.spine.${region}`, "live.spine", regionLabels[region], "region", "spine", ["live"], sortRegion(region));
    addNode(nodes, `live.spine.${region}.disc`, `live.spine.${region}`, "Επίπεδα δίσκου", "folder", "spine", ["live"], 10);
    Object.entries(regionDiscs[region]).forEach(([disc, linkedRoots], index) => {
      const linkedMuscles = unique(linkedRoots.flatMap((root) => (roots[root]?.muscles || []).map((muscle) => muscle.lat)));
      addNode(nodes, `live.spine.${region}.disc.${slug(disc)}`, `live.spine.${region}.disc`, disc, "disc_level", "spine", ["live", "academy"], index + 1, {
        linked_roots: linkedRoots,
        linked_muscles: linkedMuscles,
        summary_el: `${regionLabels[region]} επίπεδο ${disc}: ρίζες ${linkedRoots.join(" / ")}`,
      });
    });
    addNode(nodes, `live.spine.${region}.root`, `live.spine.${region}`, "Νευρικές ρίζες", "folder", "spine", ["live"], 20);
    Object.entries(roots).forEach(([root, rootData], index) => {
      addNode(nodes, `live.spine.${region}.root.${slug(root)}`, `live.spine.${region}.root`, root, "nerve_root", "spine", ["live", "academy"], index + 1, {
        linked_roots: [root],
        linked_muscles: unique(rootData.muscles.map((muscle) => muscle.lat)),
        summary_el: `${regionLabels[region]} ρίζα ${root}`,
      });
    });
  }

  addNode(nodes, "live.cranial", "live", "ΩΡΛ / Κρανιακά", "domain", "cranial", ["live"], 20);
  addNode(nodes, "live.cranial.templates", "live.cranial", "Πρότυπα επεμβάσεων", "folder", "cranial", ["live"], 10);
  Object.entries(extracted.ENT_TEMPLATES).forEach(([key, template], index) => {
    const linkedMuscles = unique(template.cns.flatMap((cn) => extracted.CRANIAL_NERVES[cn]?.muscles.map((muscle) => muscle.lat) || []));
    addNode(nodes, `live.cranial.templates.${key}`, "live.cranial.templates", template.name, "procedure_template", "cranial", ["live", "academy"], index + 1, {
      linked_cns: template.cns,
      linked_muscles: linkedMuscles,
      summary_el: template.note,
    });
  });
  addNode(nodes, "live.cranial.cn", "live.cranial", "Μεμονωμένα κρανιακά νεύρα", "folder", "cranial", ["live"], 20);
  Object.entries(extracted.CRANIAL_NERVES).forEach(([cn, data], index) => {
    addNode(nodes, `live.cranial.cn.${slug(cn)}`, "live.cranial.cn", `CN ${cn} - ${data.name}`, "cranial_nerve", "cranial", ["live", "academy"], index + 1, {
      linked_cns: [cn],
      linked_muscles: unique(data.muscles.map((muscle) => muscle.lat)),
      summary_el: data.lat,
    });
  });

  addNode(nodes, "live.ssep", "live", "SSEP", "domain", "ssep", ["live"], 30, { linked_protocols: ["protocol.ssep.warning_criteria"] });
  extracted.SSEP_DATA.forEach((item, index) => {
    addNode(nodes, `live.ssep.${slug(item.name)}`, "live.ssep", item.name, "ssep_nerve", "ssep", ["live", "academy"], index + 1, {
      linked_protocols: ["protocol.ssep.warning_criteria"],
      summary_el: `${item.code} · ${item.indication}`,
    });
  });

  addNode(nodes, "live.pedicle", "live", "Pedicle Screws", "domain", "pedicle", ["live"], 40, { linked_protocols: ["protocol.pedicle.thresholds"] });
  extracted.PEDICLE_ROOTS.forEach((item, index) => {
    addNode(nodes, `live.pedicle.${slug(item.level)}`, "live.pedicle", item.level, "pedicle_level", "pedicle", ["live", "academy"], index + 1, {
      linked_protocols: ["protocol.pedicle.thresholds"],
      summary_el: `Καταγραφή από: ${item.record}`,
    });
  });

  addNode(nodes, "live.thyroid", "live", "Θυρεοειδής", "domain", "thyroid", ["live"], 50);
  addNode(nodes, "live.thyroid.rln", "live.thyroid", "RLN", "thyroid_protocol", "thyroid", ["live", "academy"], 10, {
    linked_muscles: ["Vocalis (via NIM ETT)"],
    linked_protocols: ["protocol.thyroid.rln"],
    summary_el: "Παρακολούθηση παλίνδρομου λαρυγγικού με NIM ETT",
  });
  addNode(nodes, "live.thyroid.esln", "live.thyroid", "eSLN", "thyroid_protocol", "thyroid", ["live", "academy"], 20, {
    linked_muscles: ["Cricothyroid"],
    linked_protocols: ["protocol.thyroid.esln"],
    summary_el: "Παρακολούθηση έξω κλάδου άνω λαρυγγικού",
  });

  const academyDomains = [
    ["academy.foundations", "Foundations"],
    ["academy.spine", "Spine IONM"],
    ["academy.cranial", "Cranial nerves"],
    ["academy.thyroid", "Thyroid IONM"],
    ["academy.ssep", "SSEP interpretation"],
    ["academy.pedicle", "Pedicle screw stimulation"],
    ["academy.scenarios", "Case scenarios"],
  ];
  academyDomains.forEach(([key, title], index) => addNode(nodes, key, "academy", title, "academy_module", "academy", ["academy"], index + 1));

  return nodes;
}

function addNode(nodes, key, parentKey, title, nodeType, domain, context, sortOrder, extra = {}) {
  nodes.push({
    key,
    parent_key: parentKey,
    title_el: title,
    title_en: extra.title_en || title,
    node_type: nodeType,
    domain,
    context,
    linked_roots: extra.linked_roots || [],
    linked_muscles: extra.linked_muscles || [],
    linked_cns: extra.linked_cns || [],
    linked_protocols: extra.linked_protocols || [],
    source_ids: extra.source_ids || [],
    sort_order: sortOrder,
    status: "active",
    summary_el: extra.summary_el || null,
    metadata: extra.metadata || {},
  });
}

function buildProcedures() {
  return Object.entries(extracted.ENT_TEMPLATES).map(([key, template]) => ({
    key: `procedure.ent.${key}`,
    title_el: template.name,
    domain: "cranial",
    cranial_nerves: template.cns,
    note: template.note,
    source: "ionm-reference.html",
  }));
}

function buildModalities() {
  return ["EMG", "MEP", "SSEP", "BAEP", "t-EMG"].map((name, index) => ({
    key: `modality.${slug(name)}`,
    name,
    sort_order: index + 1,
  }));
}

function buildNerveRoots() {
  const docs = [];
  Object.entries(regionRoots).forEach(([region, roots]) => {
    Object.entries(roots).forEach(([root, data]) => {
      docs.push({
        key: `${region}.${root}`,
        region,
        root,
        muscles: data.muscles.map((muscle) => muscle.lat),
        source: "ionm-reference.html",
      });
    });
  });
  return docs;
}

function buildSpinalDiscs() {
  const docs = [];
  Object.entries(regionDiscs).forEach(([region, discs]) => {
    Object.entries(discs).forEach(([disc, roots]) => {
      docs.push({
        key: `${region}.${slug(disc)}`,
        region,
        disc,
        roots,
        source: "ionm-reference.html",
      });
    });
  });
  return docs;
}

function buildCranialNerves() {
  return Object.entries(extracted.CRANIAL_NERVES).map(([cn, data]) => ({
    key: cn,
    name_el: data.name,
    name_en: data.lat,
    muscles: data.muscles.map((muscle) => muscle.lat),
    source: "ionm-reference.html",
  }));
}

function buildMuscles() {
  const muscles = new Map();
  Object.values(regionRoots).forEach((roots) => {
    Object.values(roots).forEach((rootData) => {
      rootData.muscles.forEach((muscle) => upsertMuscle(muscles, muscle, "spine"));
    });
  });
  Object.values(extracted.CRANIAL_NERVES).forEach((cnData) => {
    cnData.muscles.forEach((muscle) => upsertMuscle(muscles, muscle, "cranial"));
  });
  return [...muscles.values()].sort((a, b) => a.lat_name.localeCompare(b.lat_name));
}

function upsertMuscle(muscles, muscle, domain) {
  const existing = muscles.get(muscle.lat) || {
    lat_name: muscle.lat,
    name_el: muscle.gr,
    domains: [],
    placements: [],
    actions: [],
    source: "ionm-reference.html",
  };
  if (!existing.domains.includes(domain)) existing.domains.push(domain);
  if (muscle.placement && !existing.placements.includes(muscle.placement)) existing.placements.push(muscle.placement);
  if (muscle.action && !existing.actions.includes(muscle.action)) existing.actions.push(muscle.action);
  existing.placement = existing.placements[0] || null;
  existing.action = existing.actions[0] || null;
  muscles.set(muscle.lat, existing);
}

function buildCoordinates() {
  return Object.entries(coordinates).map(([latName, coords]) => ({
    lat_name: latName,
    coordinates: coords,
    image: "body.jpg",
    coordinate_system: "percentage",
    source: "ionm-coords.json",
  }));
}

function buildSsepProtocols() {
  return extracted.SSEP_DATA.map((item) => ({
    key: `ssep.${slug(item.name)}`,
    ...item,
    source: "ionm-reference.html",
  }));
}

function buildPedicleRoots() {
  return extracted.PEDICLE_ROOTS.map((item) => ({
    key: `pedicle.${slug(item.level)}`,
    ...item,
    source: "ionm-reference.html",
  }));
}

function buildAcademyModules() {
  return [
    ["academy.foundations", "Foundations"],
    ["academy.spine", "Spine IONM"],
    ["academy.cranial", "Cranial nerves"],
    ["academy.thyroid", "Thyroid IONM"],
    ["academy.ssep", "SSEP interpretation"],
    ["academy.pedicle", "Pedicle screw stimulation"],
    ["academy.scenarios", "Case scenarios"],
  ].map(([moduleKey, title], index) => ({
    module_key: moduleKey,
    title,
    sort_order: index + 1,
    status: "planned",
  }));
}

function sortRegion(region) {
  return { cervical: 10, thoracic: 20, lumbar: 30 }[region] || 99;
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function slug(value) {
  return String(value)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[–—]/g, "-")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
}