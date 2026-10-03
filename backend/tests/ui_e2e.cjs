/*
 * Test de bout en bout de l'interface (Chromium sans affichage) contre un Atlas relié au VRAI Hermes.
 * Le micro est simulé par Chromium (--use-fake-device-for-media-stream : bip continu).
 * Seuls les deux appels au service vocal sont interceptés (transcription et synthèse), car les
 * modèles/services vocaux ne sont pas joignables depuis l'environnement de test.
 *
 * Usage : ATLAS_URL=http://localhost:8767 CODE=XXXX-XXXX-XXXX node tests/ui_e2e.cjs
 */
const { chromium } = require(process.env.PW || "playwright");
const fs = require("fs");

const URL = process.env.ATLAS_URL || "http://localhost:8767";
const CODE = process.env.CODE;
const OUT = process.env.SHOTS || "/tmp/atlas-shots";
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const record = (name, ok, detail = "") => {
  results.push([name, ok]);
  console.log((ok ? "  OK   " : "  ÉCHEC ") + name + (detail ? " — " + detail : ""));
};

function toneWav(seconds, freq) {
  const rate = 16000, n = rate * seconds;
  const buf = Buffer.alloc(44 + n * 2);
  buf.write("RIFF", 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write("WAVE", 8); buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24);
  buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34); buf.write("data", 36);
  buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) {
    const env = 0.5 + 0.5 * Math.sin((2 * Math.PI * i) / (rate / 3));
    buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * freq * i) / rate) * 12000 * env), 44 + i * 2);
  }
  return buf;
}

async function login(page) {
  await page.goto(URL);
  await page.waitForSelector("#code");
  await page.fill("#code", CODE);
  await page.click(".login form button");
  await page.waitForSelector(".topbar");
}

(async () => {
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, permissions: ["microphone"] });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let capturedWav = null;
  await page.route("**/api/voice/transcribe", async (route) => {
    capturedWav = route.request().postDataBuffer();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ text: "Bonjour depuis le micro", duration_s: 2, provider: "test" }) });
  });
  let speakCalls = 0;
  await page.route("**/api/voice/speak", async (route) => {
    speakCalls++;
    await route.fulfill({ status: 200, contentType: "audio/wav", body: toneWav(6, 220) });
  });

  await login(page);
  record("Connexion par code d'accès", true);
  await page.waitForSelector(".pill-ok");

  // Chat réel
  await page.fill("#composer", "Bonjour Atlas");
  await page.keyboard.press("Enter");
  await page.waitForSelector(".msg-atlas.msg-complete >> text=faux modèle", { timeout: 60000 });
  record("Message écrit → réponse réelle d'Hermes affichée", true);
  await page.screenshot({ path: `${OUT}/1-chat.png` });

  // Double envoi : deux Entrée rapides ne créent qu'un message
  const before = await page.$$eval(".msg-user", (l) => l.length);
  await page.fill("#composer", "Test double envoi");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await page.click(".send-btn", { force: true }).catch(() => {});
  await page.waitForSelector(".msg-atlas.msg-streaming", { state: "detached", timeout: 60000 });
  await page.waitForTimeout(500);
  const after = await page.$$eval(".msg-user", (l) => l.length);
  record("Protection contre le double envoi", after - before === 1, `${after - before} message(s) créé(s)`);

  // Approbation
  await page.fill("#composer", "Fais une action dangereux");
  await page.keyboard.press("Enter");
  await page.waitForSelector(".approval", { timeout: 60000 });
  const orbLabel = await page.textContent(".orb-label");
  record("Carte d'approbation + orbe « J'attends ton accord »", orbLabel.includes("accord"), orbLabel);
  await page.screenshot({ path: `${OUT}/2-approbation.png` });
  await page.click(".approval >> text=Refuser");
  await page.waitForSelector(".approval", { state: "detached", timeout: 30000 });
  await page.waitForFunction(() => document.querySelector(".orb-label").textContent === "Disponible", null, { timeout: 60000 });
  record("Refus envoyé, retour au repos", true);

  // Voix : micro → transcription → envoi → réponse → lecture sur cet appareil → coupure
  await page.click(".mic-btn");
  await page.waitForFunction(() => document.querySelector(".orb-label").textContent === "À l'écoute", null, { timeout: 5000 });
  await page.waitForTimeout(2200);
  await page.screenshot({ path: `${OUT}/3-ecoute.png` });
  await page.click(".mic-btn.recording");
  await page.waitForSelector(".msg-user >> text=Bonjour depuis le micro", { timeout: 15000 });
  const wavOk = capturedWav && capturedWav.slice(0, 4).toString() === "RIFF" && capturedWav.readUInt32LE(24) === 16000;
  record("Micro (navigateur) → WAV 16 kHz envoyé au serveur", !!wavOk, capturedWav ? `${capturedWav.length} octets` : "rien");
  fs.writeFileSync(`${OUT}/micro.wav`, capturedWav || Buffer.alloc(0));
  record("Texte reconnu affiché et envoyé à Atlas", true);
  await page.waitForFunction(() => document.querySelector(".orb-label").textContent === "Je parle", null, { timeout: 60000 });
  record("Réponse lue automatiquement sur l'appareil demandeur", speakCalls === 1);
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/4-parole.png` });
  const muted = await page.isVisible("text=Couper la voix");
  await page.click("text=Couper la voix");
  await page.waitForFunction(() => document.querySelector(".orb-label").textContent === "Disponible", null, { timeout: 5000 });
  record("« Couper la voix » arrête la lecture et l'orbe revient au repos", muted);

  // Notes et priorités
  await page.click('button[aria-label="Nouvelle note"]');
  await page.fill(".note-editor textarea", "Acheter du café");
  await page.waitForSelector(".save-saved", { timeout: 5000 });
  record("Note créée et enregistrée automatiquement", true);
  await page.click(".note-editor >> text=Fermer");
  await page.click("text=Ajouter une priorité");
  await page.fill("#prio-title", "Finir le dossier client");
  await page.click(".prio-form >> text=Enregistrer");
  await page.waitForSelector(".prio-title >> text=Finir le dossier client");
  await page.click(".prio-check");
  await page.waitForSelector(".prio-done");
  record("Priorité ajoutée et cochée manuellement", true);
  await page.screenshot({ path: `${OUT}/5-journee.png` });

  // Activité
  await page.click('button[aria-label="Ouvrir le panneau d\'activité"]');
  await page.waitForSelector(".activity-list li");
  const acts = await page.$$eval(".act-label", (l) => l.map((x) => x.textContent));
  record("Panneau d'activité alimenté par des événements réels", acts.some((a) => a.includes("Approbation demandée")) && acts.some((a) => a.includes("Lecture audio")), `${acts.length} événements`);
  await page.screenshot({ path: `${OUT}/6-activite.png` });
  await page.keyboard.press("Escape");

  // Réglages
  await page.click('button[aria-label="Ouvrir les réglages"]');
  await page.click(".tab >> text=Connexion");
  await page.screenshot({ path: `${OUT}/7-reglages.png` });
  const conn = await page.textContent(".modal-body");
  record("Réglages : intégration et modèle rapportés par Hermes", conn.includes("sessions") && conn.includes("fake-model"));
  await page.keyboard.press("Escape");

  // Micro refusé
  const ctx2 = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  const p2 = await ctx2.newPage();
  await p2.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = () => Promise.reject(Object.assign(new Error("denied"), { name: "NotAllowedError" }));
  });
  await login(p2);
  await p2.click(".mic-btn");
  await p2.waitForSelector(".toast >> text=Accès au micro refusé", { timeout: 5000 });
  record("Micro refusé : message clair", true);
  await ctx2.close();

  // Synthèse indisponible : la réponse écrite reste
  await page.unroute("**/api/voice/speak");
  const firstAtlas = await page.$(".msg-atlas.msg-complete");
  await firstAtlas.hover();
  await firstAtlas.$eval('button[aria-label="Écouter cette réponse"]', (b) => b.click());
  await page.waitForSelector(".toast >> text=Voix indisponible", { timeout: 15000 });
  record("Synthèse indisponible : message clair, texte conservé", (await page.$$(".msg-atlas.msg-complete")).length > 0);

  // Mobile
  const ctx3 = await browser.newContext({ viewport: { width: 390, height: 844 }, permissions: ["microphone"] });
  const p3 = await ctx3.newPage();
  await login(p3);
  await p3.waitForTimeout(800);
  await p3.screenshot({ path: `${OUT}/8-mobile.png` });
  const overflow = await p3.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  await p3.click(".mobile-nav >> text=Journée");
  await p3.screenshot({ path: `${OUT}/9-mobile-journee.png` });
  record("Affichage mobile sans débordement horizontal", !overflow);
  await ctx3.close();

  record("Aucune erreur JavaScript", errors.length === 0, errors.join(" | "));
  await browser.close();
  const failed = results.filter((r) => !r[1]).length;
  console.log(`\n${results.length - failed}/${results.length} vérifications d'interface réussies.`);
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
