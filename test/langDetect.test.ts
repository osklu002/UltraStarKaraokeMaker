// Testes do src/langDetect.ts - rodar com `npm test` (Node 22+, sem
// dependência: o próprio Node remove os tipos do TypeScript).
// Os textos são frases escritas para o teste, não letras de verdade.
import { test } from "node:test";
import assert from "node:assert/strict";
import { detectLyricsLanguage, suggestLanguage } from "../src/langDetect.ts";

const EN = `I walk the road at night and you are on my mind
I know that we can never stop, it's all we have
And when the morning comes I want to see your face
So take my hand, don't let me go, I'm here for you
We sing it loud and all the city hears the sound`;

const SV = `Jag går hem genom stan och tänker på dig
Det är inte lätt att veta vad jag ska säga
Men när du ler så vill jag bara stanna här
Vi har allt som vi behöver, du och jag
Och natten är så lång när du inte är med mig`;

const PT = `Eu ando pela rua e penso em você
Não sei o que dizer quando a noite vem
Mas quando você sorri eu quero ficar aqui
Tudo que eu tenho é seu, meu amor
E a cidade dorme sem saber do nosso amor`;

const ES = `Yo camino por la calle y pienso en ti
No sé qué decir cuando la noche llega
Pero cuando sonríes quiero quedarme aquí
Todo lo que tengo es para ti, mi amor
Y la ciudad duerme sin saber de los dos`;

const DE = `Ich gehe durch die Stadt und denke nur an dich
Es ist nicht leicht zu wissen, was ich sagen will
Aber wenn du lachst, dann will ich hier bei dir sein
Wir haben alles, was wir brauchen, du und ich
Und die Nacht ist lang, wenn du nicht bei mir bist`;

const NO = `Jeg går hjem gjennom byen og tenker på deg
Det er ikke lett å vite hva jeg skal si
Men når du smiler vil jeg bare være her
Vi har alt vi trenger, du og jeg
Og natten er så lang når du ikke er med meg`;

const RU = `Я иду по улице и думаю о тебе
Ночь длинна, когда тебя нет рядом со мной
Мы поём громко, и весь город слышит нас`;

const UK = `Я іду вулицею і думаю про тебе
Ніч довга, коли тебе немає поруч зі мною
Ми співаємо голосно, і все місто чує нас`;

test("recognizes clearly different Latin-script languages", () => {
  assert.equal(detectLyricsLanguage(EN)?.code, "en");
  assert.equal(detectLyricsLanguage(SV)?.code, "sv");
  assert.equal(detectLyricsLanguage(PT)?.code, "pt");
  assert.equal(detectLyricsLanguage(ES)?.code, "es");
  assert.equal(detectLyricsLanguage(DE)?.code, "de");
});

test("Swedish and Norwegian are told apart", () => {
  assert.equal(detectLyricsLanguage(NO)?.code, "no");
  assert.equal(suggestLanguage("no", SV), "sv");
  assert.equal(suggestLanguage("sv", NO), "no");
});

test("recognizes scripts, and Ukrainian by its own letters", () => {
  assert.equal(detectLyricsLanguage(RU)?.code, "ru");
  assert.equal(detectLyricsLanguage(UK)?.code, "uk");
  assert.equal(detectLyricsLanguage("君の声が聞こえる。夜の街で、ずっと待っていた。忘れないよ。")?.code, "ja");
  assert.equal(detectLyricsLanguage("너의 목소리가 들려 밤의 거리에서 계속 기다렸어 잊지 않을게")?.code, "ko");
});

test("suggests a switch only when the selected language is wrong", () => {
  assert.equal(suggestLanguage("en", SV), "sv");
  assert.equal(suggestLanguage("pt", EN), "en");
  assert.equal(suggestLanguage("sv", SV), null);
  assert.equal(suggestLanguage("en", EN), null);
});

test("no warning inside a group of close languages", () => {
  assert.equal(suggestLanguage("da", NO), null);
  assert.equal(suggestLanguage("nn", NO), null);
});

test("same script: Russian and Ukrainian are told apart, Arabic-script languages are not", () => {
  // russo x ucraniano se separam pelas letras próprias; árabe x persa x
  // urdu não com segurança - mesma escrita, sem aviso
  assert.equal(suggestLanguage("uk", RU), "ru");
  assert.equal(suggestLanguage("ru", UK), "uk");
  assert.equal(suggestLanguage("ru", RU), null);
  // cirílico sem letra própria de nenhum dos dois: não afirma nada
  assert.equal(detectLyricsLanguage("Мама папа дома тут там как так да нет вот мир сон день ночь"), null);
  assert.equal(suggestLanguage("fa", "سلام دنیا این یک متن کوتاه برای آزمایش است که باید شناخته شود"), null);
});

test("romanized lyrics with a non-Latin language selected: left to the existing hint", () => {
  assert.equal(suggestLanguage("ja", EN), null);
});

test("says nothing when unsure", () => {
  assert.equal(detectLyricsLanguage(""), null);
  assert.equal(detectLyricsLanguage("I love you baby"), null); // curto demais
  assert.equal(detectLyricsLanguage(Array(40).fill("la la la na na").join("\n")), null);
});

test("ignores .lrc timestamps, section tags and duet markers", () => {
  const tagged = SV.split("\n").map((l, i) => `[00:${10 + i}.00]P1: ${l}`).join("\n");
  assert.equal(detectLyricsLanguage(`[Refräng]\n${tagged}`)?.code, "sv");
});
