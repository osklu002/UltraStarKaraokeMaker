// Detecção do idioma da LETRA, para avisar quando o idioma escolhido não bate.
//
// Por que existe: o idioma é uma preferência salva (padrão: português), então
// é fácil gerar uma música sueca com o idioma em "English" sem perceber - e
// ele decide o modelo de alinhamento (sueco tem modelo próprio), a divisão
// silábica e o Whisper. Nada avisava.
//
// Como: sem dependência e sem rede, em dois passos.
//   1. Escrita: letra majoritariamente árabe, hebraica, grega, cirílica,
//      CJK, hangul, devanágari, georgiana, malaiala ou télugo já diz o idioma
//      (ou o grupo, quando a escrita é compartilhada).
//   2. Alfabeto latino: conta as palavras mais frequentes de cada idioma
//      ("och", "jag", "inte" no sueco; "the", "and", "you" no inglês). Só
//      responde com folga sobre o segundo colocado; idiomas muito próximos
//      (dinamarquês/norueguês, tcheco/eslovaco...) formam um grupo e nunca
//      geram aviso entre si.
// Na dúvida devolve null - um aviso errado é pior que nenhum.

export type DetectedLanguage = { code: string; script: string };

// Palavras curtas e frequentes (artigos, pronomes, conjunções, verbos
// auxiliares) - as que aparecem em qualquer letra, de qualquer tema.
const STOPWORDS: Record<string, string[]> = {
  en: ["the", "and", "you", "i", "me", "my", "to", "of", "in", "it", "is", "that", "on", "for", "your", "with", "be", "are", "was", "all", "so", "we", "but", "not", "don't", "can", "what", "this", "just", "oh", "know", "when", "like", "now", "never", "i'm", "it's", "you're", "can't", "love"],
  sv: ["och", "jag", "inte", "är", "det", "ett", "att", "som", "på", "med", "för", "av", "till", "du", "dig", "mig", "min", "mitt", "vi", "men", "när", "så", "har", "var", "vill", "kan", "bara", "aldrig", "här", "där", "nu", "ska", "hur", "allt", "mot", "ut", "upp", "sig", "ingen", "mej", "dej"],
  no: ["og", "jeg", "ikke", "er", "det", "et", "å", "på", "med", "for", "til", "du", "deg", "meg", "min", "vi", "men", "når", "så", "har", "var", "vil", "kan", "bare", "aldri", "her", "der", "nå", "skal", "hvor", "alt", "mot", "ut", "opp", "seg", "ingen"],
  da: ["og", "jeg", "ikke", "er", "det", "et", "at", "på", "med", "for", "til", "du", "dig", "mig", "min", "vi", "men", "når", "så", "har", "var", "vil", "kan", "bare", "aldrig", "her", "der", "nu", "skal", "hvor", "alt", "mod", "ud", "op", "sig", "ingen"],
  de: ["und", "ich", "nicht", "ist", "das", "die", "der", "ein", "eine", "zu", "mit", "für", "auf", "du", "dich", "mich", "mein", "wir", "aber", "wenn", "hab", "habe", "war", "will", "kann", "nur", "nie", "hier", "jetzt", "wie", "alles", "sie", "es", "uns", "noch", "auch", "im", "dem", "dir", "mir"],
  nl: ["en", "ik", "niet", "is", "het", "de", "een", "te", "met", "voor", "op", "je", "jij", "mij", "me", "mijn", "wij", "we", "maar", "als", "zo", "heb", "was", "wil", "kan", "nooit", "hier", "daar", "nu", "hoe", "alles", "zijn", "er", "dat", "van", "ook", "nog", "wat", "jou"],
  fr: ["et", "je", "ne", "pas", "est", "le", "la", "les", "un", "une", "de", "des", "à", "avec", "pour", "sur", "tu", "toi", "moi", "mon", "ma", "nous", "mais", "si", "quand", "c'est", "j'ai", "plus", "jamais", "ici", "comme", "tout", "que", "qui", "dans", "il", "elle", "on", "au", "suis"],
  es: ["y", "yo", "no", "es", "el", "la", "los", "las", "un", "una", "de", "que", "con", "para", "por", "en", "tú", "te", "me", "mi", "mis", "pero", "si", "cuando", "más", "nunca", "aquí", "como", "todo", "qué", "del", "al", "lo", "se", "su", "mí", "eres", "quiero", "estoy", "amor"],
  pt: ["e", "eu", "não", "é", "o", "os", "as", "um", "uma", "de", "que", "com", "para", "por", "em", "você", "te", "me", "meu", "minha", "mas", "se", "quando", "mais", "nunca", "aqui", "como", "tudo", "do", "da", "no", "na", "ao", "seu", "sua", "sou", "quero", "estou", "amor", "vou"],
  ca: ["i", "jo", "no", "és", "el", "la", "els", "les", "un", "una", "de", "que", "amb", "per", "en", "tu", "et", "em", "meu", "meva", "però", "si", "quan", "més", "mai", "aquí", "com", "tot", "del", "al", "ho", "es", "sóc", "vull", "som"],
  it: ["e", "io", "non", "è", "il", "la", "lo", "gli", "le", "un", "una", "di", "che", "con", "per", "in", "tu", "ti", "mi", "mio", "mia", "noi", "ma", "se", "quando", "più", "mai", "qui", "come", "tutto", "sei", "sono", "del", "della", "nel", "ho", "cosa", "ancora"],
  fi: ["ja", "minä", "mä", "sinä", "sä", "ei", "on", "se", "että", "kun", "niin", "mutta", "olen", "olet", "oli", "tämä", "kuin", "en", "et", "vain", "nyt", "aina", "sun", "mun", "mua", "sua", "sitä", "mitä", "kaikki", "kanssa", "vielä", "jos"],
  pl: ["i", "ja", "nie", "jest", "to", "w", "z", "na", "że", "się", "ty", "mnie", "mi", "mój", "my", "ale", "jak", "kiedy", "tak", "już", "tylko", "co", "czy", "do", "po", "jestem", "jesteś", "bo", "nas", "tu"],
  cs: ["a", "já", "ne", "je", "to", "v", "z", "na", "že", "se", "ty", "mě", "mi", "můj", "my", "ale", "jak", "když", "tak", "už", "jen", "co", "do", "po", "jsem", "jsi", "být", "tě", "si", "jsme"],
  sk: ["a", "ja", "nie", "je", "to", "v", "z", "na", "že", "sa", "ty", "ma", "mi", "môj", "my", "ale", "ako", "keď", "tak", "už", "len", "čo", "do", "po", "som", "si", "byť", "ťa"],
  hr: ["i", "ja", "ne", "je", "to", "u", "s", "na", "da", "se", "ti", "me", "mi", "moj", "ali", "kao", "kad", "tako", "već", "samo", "što", "sam", "si", "smo", "nije", "sve"],
  sl: ["in", "jaz", "ne", "je", "to", "v", "z", "na", "da", "se", "ti", "me", "mi", "moj", "ampak", "kot", "ko", "tako", "že", "samo", "kaj", "sem", "si", "smo", "ni", "vse"],
  hu: ["és", "én", "nem", "az", "a", "egy", "hogy", "van", "te", "de", "ha", "mert", "csak", "még", "már", "meg", "is", "vagy", "mint", "ez", "itt", "minden", "nincs", "vagyok", "engem", "téged"],
  ro: ["și", "eu", "nu", "e", "este", "un", "o", "de", "că", "cu", "pentru", "pe", "în", "tu", "te", "mă", "meu", "mea", "noi", "dar", "dacă", "când", "mai", "niciodată", "aici", "ca", "tot", "sunt", "ești", "la"],
  tr: ["ve", "ben", "bir", "bu", "ne", "sen", "seni", "beni", "için", "ama", "gibi", "çok", "var", "yok", "da", "de", "mi", "ki", "her", "şey", "kadar", "daha", "artık", "değil", "benim", "senin"],
  id: ["dan", "aku", "tidak", "tak", "ini", "itu", "yang", "di", "ke", "dari", "kau", "kamu", "dengan", "untuk", "akan", "ada", "cinta", "tapi", "jika", "saat", "hanya", "selalu", "kita", "kami", "dia", "lagi", "bisa", "sudah"],
  // sem "na", "ka", "pa": são as sílabas de enchimento ("na na na", "Europa-pa")
  tl: ["ang", "ng", "sa", "at", "ko", "mo", "ako", "ikaw", "hindi", "ay", "mga", "ito", "kung", "lang", "din", "rin", "akin", "iyo", "natin", "tayo", "siya"],
  lv: ["un", "es", "ne", "ir", "tu", "man", "mani", "tevi", "kā", "bet", "ja", "kad", "tik", "vēl", "jau", "visu", "no", "uz", "par", "ar", "mēs", "jūs", "viņš", "viņa", "šo", "to", "sevi"],
  eu: ["eta", "ez", "da", "naiz", "zara", "ni", "zu", "gu", "baina", "bat", "hau", "hori", "dut", "duzu", "dago", "zen", "ere", "oso", "gabe", "nire", "zure", "maite"],
};
const STOPWORD_SETS = Object.fromEntries(
  Object.entries(STOPWORDS).map(([code, words]) => [code, new Set(words)])
);

// Peso de cada palavra = 1 / em quantas listas ela está. "och" só é sueca e
// vale 1; "det" é sueca, norueguesa e dinamarquesa e vale 1/3; o "la" de "la
// la la" está em quatro listas. Sem isto as palavras comuns aos três idiomas
// escandinavos empatavam o sueco com o norueguês ("Det gör ont": sueco 94,
// dinamarquês 61 na contagem simples).
const WORD_WEIGHT = new Map<string, number>();
for (const words of Object.values(STOPWORDS)) {
  for (const w of new Set(words)) WORD_WEIGHT.set(w, (WORD_WEIGHT.get(w) ?? 0) + 1);
}

// Idiomas próximos demais para a contagem de palavras separar com segurança:
// entre membros do mesmo grupo nunca há aviso.
const CLOSE_GROUPS: string[][] = [
  ["da", "no", "nn"],
  ["cs", "sk"],
  ["hr", "sl"],
  ["pt", "gl"],
  ["es", "ca", "gl"],
];

export function sameLanguageGroup(a: string, b: string): boolean {
  return a === b || CLOSE_GROUPS.some((g) => g.includes(a) && g.includes(b));
}

// Escrita de cada idioma não latino (os da lista do app).
export const LANGUAGE_SCRIPT: Record<string, string> = {
  ar: "arabic", fa: "arabic", ur: "arabic", he: "hebrew", el: "greek",
  ru: "cyrillic", uk: "cyrillic", ja: "cjk", zh: "cjk", ko: "hangul",
  hi: "devanagari", ka: "georgian", ml: "malayalam", te: "telugu",
};

const SCRIPT_RANGES: [string, RegExp][] = [
  ["arabic", /[؀-ۿݐ-ݿ]/u],
  ["hebrew", /[֐-׿]/u],
  ["greek", /[Ͱ-Ͽ]/u],
  ["cyrillic", /[Ѐ-ӿ]/u],
  ["kana", /[぀-ヿ]/u],
  ["han", /[一-鿿]/u],
  ["hangul", /[가-힯ᄀ-ᇿ]/u],
  ["devanagari", /[ऀ-ॿ]/u],
  ["georgian", /[Ⴀ-ჿ]/u],
  ["malayalam", /[ഀ-ൿ]/u],
  ["telugu", /[ఀ-౿]/u],
];

// Letras do vietnamita que nenhum outro idioma latino da lista usa.
const VIETNAMESE = /[ăơưđạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]/u;

// Limites conferidos nas 529 letras feitas à mão de uma biblioteca en/sv (ver
// o PR): com eles, nenhum aviso falso com o idioma certo selecionado.
const MIN_WORDS = 20;     // menos que isso não dá para afirmar nada
const MIN_HITS = 6;       // pontos (palavras pesadas) do idioma vencedor
const MIN_SHARE = 0.10;   // pontos / palavras da letra - finlandês, com
                          // palavras longas e flexionadas, fica perto de 0,15
const MIN_LEAD = 1.6;     // folga sobre o segundo (fora do grupo dele)

/** Texto da letra sem marcações: tempos de .lrc, [Refrão], P1:/P2:. */
function lyricsOnly(text: string): string {
  return text
    .replace(/\[[^\]]*\]/g, " ")
    .replace(/^\s*P\d(&P\d)?\s*:/gim, " ");
}

function detectScript(text: string): { script: string; share: number } | null {
  const letters = Array.from(text).filter((c) => /\p{L}/u.test(c));
  if (letters.length < 15) return null;
  const counts = new Map<string, number>();
  for (const c of letters) {
    const hit = SCRIPT_RANGES.find(([, re]) => re.test(c));
    const name = hit ? hit[0] : /\p{Script=Latin}/u.test(c) ? "latin" : "other";
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  // kana + han = japonês; han sozinho = chinês
  const kana = counts.get("kana") ?? 0;
  const han = counts.get("han") ?? 0;
  if (kana + han > 0) {
    counts.delete("kana");
    counts.delete("han");
    counts.set(kana > 0 ? "cjk-ja" : "cjk-zh", kana + han);
  }
  const [script, n] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  return { script, share: n / letters.length };
}

function detectNonLatin(script: string, text: string): string | null {
  switch (script) {
    case "arabic":
      if (/[ٹڈڑںےھ]/u.test(text)) return "ur";
      if (/[پچژگ]/u.test(text)) return "fa";
      return "ar";
    case "cyrillic": {
      // cada um tem letras que o outro não usa; sem elas, não dá para dizer
      const uk = (text.match(/[іїєґ]/giu) ?? []).length;
      const ru = (text.match(/[ыэъё]/giu) ?? []).length;
      if (uk >= 3 && uk > ru) return "uk";
      if (ru >= 3 && ru > uk) return "ru";
      return null;
    }
    case "cjk-ja": return "ja";
    case "cjk-zh": return "zh";
    case "hebrew": return "he";
    case "greek": return "el";
    case "hangul": return "ko";
    case "devanagari": return "hi";
    case "georgian": return "ka";
    case "malayalam": return "ml";
    case "telugu": return "te";
    default: return null;
  }
}

function latinWords(text: string): string[] {
  const lower = text.toLowerCase().replace(/[’`]/g, "'");
  return lower.match(/[\p{L}']+/gu)?.map((w) => w.replace(/^'+|'+$/g, "")).filter(Boolean) ?? [];
}

/** Pontos de cada idioma na letra (palavras frequentes, pesadas por
 *  WORD_WEIGHT), maior primeiro. `words` = total de palavras da letra. */
export function languageScores(text: string): { code: string; n: number; words: number }[] {
  const words = latinWords(text);
  return Object.entries(STOPWORD_SETS)
    .map(([code, set]) => ({
      code,
      n: words.reduce((sum, w) => sum + (set.has(w) ? 1 / WORD_WEIGHT.get(w)! : 0), 0),
      words: words.length,
    }))
    .sort((a, b) => b.n - a.n);
}

function detectLatin(text: string): string | null {
  const words = latinWords(text);
  if (words.length < MIN_WORDS) return null;
  const vietnamese = Array.from(text.toLowerCase()).filter((c) => VIETNAMESE.test(c)).length;
  if (vietnamese >= 10) return "vi";
  const hits = languageScores(text);
  const top = hits[0];
  const rival = hits.find((h) => !sameLanguageGroup(h.code, top.code));
  if (top.n < MIN_HITS || top.n / words.length < MIN_SHARE) return null;
  if (rival && top.n < MIN_LEAD * rival.n) return null;
  return top.code;
}

/** Idioma provável da letra, ou null quando não dá para dizer com folga. */
export function detectLyricsLanguage(text: string): DetectedLanguage | null {
  const clean = lyricsOnly(text);
  const s = detectScript(clean);
  if (!s || s.share < 0.6) return null;
  if (s.script === "latin") {
    const code = detectLatin(clean);
    return code ? { code, script: "latin" } : null;
  }
  const code = detectNonLatin(s.script, clean);
  return code ? { code, script: LANGUAGE_SCRIPT[code] } : null;
}

// Escritas compartilhadas em que os idiomas se separam com segurança pelas
// letras próprias de cada um (russo ы э ъ ё, ucraniano і ї є ґ). Árabe, persa
// e urdu, ou japonês e chinês, ficam de fora: dentro deles, nada de aviso.
const SEPARABLE_SCRIPTS = new Set(["cyrillic"]);

/**
 * O idioma a sugerir no lugar de `selected`, ou null se não há o que avisar:
 * nada detectado, mesmo idioma/grupo, ou a mesma escrita não latina que não
 * se separa com segurança (SEPARABLE_SCRIPTS). Letra latina com idioma de
 * escrita não latina selecionado fica com o aviso que o app já tem
 * (romanização).
 */
export function suggestLanguage(selected: string, lyrics: string): string | null {
  const d = detectLyricsLanguage(lyrics);
  if (!d || sameLanguageGroup(d.code, selected)) return null;
  const selectedScript = LANGUAGE_SCRIPT[selected] ?? "latin";
  if (d.script === "latin" && selectedScript !== "latin") return null;
  if (d.script !== "latin" && d.script === selectedScript && !SEPARABLE_SCRIPTS.has(d.script)) return null;
  return d.code;
}
