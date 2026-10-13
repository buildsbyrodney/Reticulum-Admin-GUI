// Generate the offline KJV lookup catalog from OpenBible's tab-delimited text.
// Source: https://openbible.com/textfiles/kjv.txt
// Header attribution: King James Bible: Pure Cambridge Edition — BibleProtector.com.
import fs from "node:fs/promises";
import path from "node:path";

const SOURCE_URL = "https://openbible.com/textfiles/kjv.txt";
const OUTPUT = path.resolve("lxmf-test-bot/kjv.json");
const CANONICAL_BOOKS = [
  "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges", "Ruth",
  "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah",
  "Esther", "Job", "Psalm", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah",
  "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah",
  "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi", "Matthew", "Mark", "Luke",
  "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians", "Philippians",
  "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus", "Philemon",
  "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John", "Jude", "Revelation",
];
const sourcePath = process.argv.indexOf("--source");
const localSource = sourcePath >= 0 ? process.argv[sourcePath + 1] : null;
if (sourcePath >= 0 && !localSource) throw new Error("--source requires a file path");

async function readSource() {
  if (localSource) return fs.readFile(path.resolve(localSource), "utf8");
  const response = await fetch(SOURCE_URL);
  if (!response.ok) throw new Error(`Could not download KJV source: HTTP ${response.status}`);
  return response.text();
}

function parse(text) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
  if (lines[0] !== "KJV" || !lines[1]?.includes("King James Bible:" )) {
    throw new Error("Unexpected KJV source header");
  }
  const verses = {};
  const books = new Set();
  const expectedBook = new Map(CANONICAL_BOOKS.map((book, index) => [book, index]));
  let lastBook = -1;
  let lastChapter = 0;
  let lastVerse = 0;
  for (let index = 2; index < lines.length; index++) {
    const line = lines[index];
    if (!line) continue;
    const match = line.match(/^(.*?) (\d+):(\d+)\t(.+)$/);
    if (!match) throw new Error(`Malformed verse line ${index + 1}`);
    const [, book, chapterText, verseText, verse] = match;
    const bookIndex = expectedBook.get(book);
    if (bookIndex === undefined) throw new Error(`Unexpected source book on line ${index + 1}: ${book}`);
    const chapter = Number(chapterText);
    const number = Number(verseText);
    if (!verse.trim() || !Number.isInteger(chapter) || chapter < 1 || !Number.isInteger(number) || number < 1) {
      throw new Error(`Invalid verse content on line ${index + 1}`);
    }
    if (bookIndex < lastBook || (bookIndex === lastBook && (chapter < lastChapter || (chapter === lastChapter && number <= lastVerse)))) {
      throw new Error(`Unexpected verse order on line ${index + 1}`);
    }
    if ((bookIndex > lastBook && (chapter !== 1 || number !== 1)) ||
        (bookIndex === lastBook && chapter > lastChapter && (chapter !== lastChapter + 1 || number !== 1)) ||
        (bookIndex === lastBook && chapter === lastChapter && number !== lastVerse + 1)) {
      throw new Error(`Unexpected book, chapter, or verse sequence on line ${index + 1}`);
    }
    const reference = `${book} ${chapter}:${number}`;
    if (verses[reference]) throw new Error(`Duplicate canonical reference: ${reference}`);
    verses[reference] = verse;
    books.add(book);
    lastBook = bookIndex;
    lastChapter = chapter;
    lastVerse = number;
  }
  if (books.size !== CANONICAL_BOOKS.length || CANONICAL_BOOKS.some((book) => !books.has(book))) {
    throw new Error("Source does not contain the complete 66-book Protestant canon");
  }
  const count = Object.keys(verses).length;
  if (count < 30000 || count > 32000) throw new Error(`Implausible complete-KJV verse count: ${count}`);
  return { books: CANONICAL_BOOKS, verses };
}

const parsed = parse(await readSource());
const generated = {
  source: {
    url: SOURCE_URL,
    edition: "King James Bible: Pure Cambridge Edition",
    attribution: "Text courtesy of BibleProtector.com via OpenBible.com",
    license_note: "Preserve source attribution and exact Bible text when redistributing generated data.",
    source_format: "One tab-delimited canonical reference and verse text per line",
  },
  books: parsed.books,
  verses: parsed.verses,
};
if (process.argv.includes("--check")) {
  const bundled = JSON.parse(await fs.readFile(OUTPUT, "utf8"));
  if (JSON.stringify(bundled) !== JSON.stringify(generated)) {
    throw new Error("Bundled KJV catalog differs from the canonical source output");
  }
  console.log(`Verified ${Object.keys(parsed.verses).length} canonical KJV verses across ${parsed.books.length} books`);
} else {
  await fs.writeFile(OUTPUT, JSON.stringify(generated, null, 2) + "\n", "utf8");
  console.log(`Generated ${Object.keys(parsed.verses).length} canonical KJV verses across ${parsed.books.length} books`);
}
