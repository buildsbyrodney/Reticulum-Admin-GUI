// Canonical source: https://github.com/midvash/bible-data
// versions/en/kjv/kjv.json — 1769 public-domain KJV.
import fs from "node:fs/promises";
import path from "node:path";

const refs = `Genesis 1:1,Genesis 1:27,Genesis 15:1,Exodus 14:14,Deuteronomy 31:6,Joshua 1:9,1 Samuel 16:7,Job 19:25,Psalm 1:1,Psalm 4:8,Psalm 16:11,Psalm 18:2,Psalm 19:1,Psalm 23:1,Psalm 23:4,Psalm 27:1,Psalm 27:14,Psalm 34:8,Psalm 34:17,Psalm 37:4,Psalm 37:5,Psalm 46:1,Psalm 46:10,Psalm 51:10,Psalm 56:3,Psalm 61:2,Psalm 91:1,Psalm 91:2,Psalm 103:2,Psalm 118:24,Psalm 119:105,Psalm 121:1,Psalm 121:2,Psalm 139:14,Proverbs 3:5,Proverbs 3:6,Proverbs 4:23,Proverbs 16:3,Proverbs 17:17,Proverbs 18:10,Proverbs 27:17,Song of Solomon 2:1,Isaiah 9:6,Isaiah 40:31,Isaiah 41:10,Isaiah 43:2,Isaiah 55:8,Jeremiah 17:7,Jeremiah 29:11,Lamentations 3:22,Lamentations 3:23,Micah 6:8,Nahum 1:7,Matthew 5:14,Matthew 6:33,Matthew 7:7,Matthew 11:28,Matthew 19:26,Matthew 22:37,Matthew 28:19,Mark 10:27,Luke 1:37,Luke 6:31,John 1:1,John 3:16,John 8:32,John 10:10,John 11:25,John 13:34,John 14:1,John 14:6,John 14:27,John 15:5,Acts 1:8,Acts 4:12,Acts 16:31,Romans 3:23,Romans 5:8,Romans 6:23,Romans 8:1,Romans 8:28,Romans 10:9,Romans 12:2,1 Corinthians 10:13,1 Corinthians 13:4,1 Corinthians 13:13,2 Corinthians 5:17,2 Corinthians 12:9,Galatians 2:20,Galatians 5:22,Ephesians 2:8,Ephesians 2:9,Ephesians 3:20,Ephesians 4:32,Ephesians 6:10,Philippians 4:4,Philippians 4:6,Philippians 4:7,Philippians 4:13,Colossians 3:15,1 Thessalonians 5:16,1 Thessalonians 5:17,1 Thessalonians 5:18,2 Timothy 1:7,2 Timothy 3:16,Hebrews 11:1,Hebrews 11:6,Hebrews 12:1,Hebrews 13:5,James 1:5,James 1:17,1 Peter 5:7,1 John 1:9,1 John 4:8,1 John 4:19,Revelation 3:20,Revelation 21:4`.split(",");
// OSIS codes are fixed identifiers, never guessed from English filenames.
const OSIS = {Genesis:"Gen",Exodus:"Exod",Leviticus:"Lev",Numbers:"Num",Deuteronomy:"Deut",Joshua:"Josh",Judges:"Judg",Ruth:"Ruth","1 Samuel":"1Sam","2 Samuel":"2Sam","1 Kings":"1Kgs","2 Kings":"2Kgs","1 Chronicles":"1Chr","2 Chronicles":"2Chr",Ezra:"Ezra",Nehemiah:"Neh",Esther:"Esth",Job:"Job",Psalm:"Ps",Proverbs:"Prov",Ecclesiastes:"Eccl","Song of Solomon":"Song",Isaiah:"Isa",Jeremiah:"Jer",Lamentations:"Lam",Ezekiel:"Ezek",Daniel:"Dan",Hosea:"Hos",Joel:"Joel",Amos:"Amos",Obadiah:"Obad",Jonah:"Jonah",Micah:"Mic",Nahum:"Nah",Habakkuk:"Hab",Zephaniah:"Zeph",Haggai:"Hag",Zechariah:"Zech",Malachi:"Mal",Matthew:"Matt",Mark:"Mark",Luke:"Luke",John:"John",Acts:"Acts",Romans:"Rom","1 Corinthians":"1Cor","2 Corinthians":"2Cor",Galatians:"Gal",Ephesians:"Eph",Philippians:"Phil",Colossians:"Col","1 Thessalonians":"1Thess","2 Thessalonians":"2Thess","1 Timothy":"1Tim","2 Timothy":"2Tim",Titus:"Titus",Philemon:"Phlm",Hebrews:"Heb",James:"Jas","1 Peter":"1Pet","2 Peter":"2Pet","1 John":"1John","2 John":"2John","3 John":"3John",Jude:"Jude",Revelation:"Rev"};
const source = await (await fetch("https://raw.githubusercontent.com/midvash/bible-data/main/versions/en/kjv/kjv.json")).json();
const books = source.books || source;
const result = {};
for (const raw of refs) {
  const m = raw.match(/^(.*) (\d+):(\d+)$/); const book = m[1], chapter = +m[2], number = +m[3];
  const osis = OSIS[book]; if (!osis) throw new Error(`No OSIS mapping: ${book}`);
  const data = books.find(x => x.book === osis || x.englishName === book);
  const found = data?.chapters?.find(x => x.chapter === chapter)?.verses?.find(x => x.number === number);
  if (!found?.text?.trim()) throw new Error(`Missing or blank source verse: ${raw}`);
  if (result[raw]) throw new Error(`Duplicate canonical reference: ${raw}`);
  result[raw] = found.text;
}
if (Object.keys(result).length < 100) throw new Error("Fewer than 100 canonical KJV references");
const output = path.resolve("lxmf-test-bot/kjv.json");
const generated = {source:"midvash/bible-data versions/en/kjv/kjv.json", version:"kjv 1769", verses:result};
if (process.argv.includes("--check")) {
  const bundled = JSON.parse(await fs.readFile(output, "utf8"));
  if (JSON.stringify(bundled.verses) !== JSON.stringify(result)) throw new Error("Bundled KJV content differs from canonical source output");
  console.log(`Verified ${Object.keys(result).length} canonical KJV verses`);
} else {
  await fs.writeFile(output, JSON.stringify(generated, null, 2)+"\n");
  console.log(`Generated ${Object.keys(result).length} canonical KJV verses`);
}
