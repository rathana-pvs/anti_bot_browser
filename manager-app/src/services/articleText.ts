/** Words a news page puts between paragraphs; matched against a whole line only, so article sentences are never touched. */
const PAGE_LINES = new Set(['advertisement', 'ad', 'sponsored', 'recommended stories',
  'read more', 'read also', 'also read', 'read next', 'see also', 'more stories', 'trending',
  'share', 'share this', 'share this article', 'click here to share on social media', 'save', 'save article',
  'listen', 'listen to this article', 'subscribe', 'sign up', 'newsletter', 'follow us', 'skip to content', 'skip to main content',
  'skip links', 'continue reading', 'scroll to continue', 'story continues below', 'article continues below advertisement']);
const PROMPT = /^get instant alerts\b|keep me updated$|^[*•-]?\s*list \d+ of \d+|^(list of \d+ items|end of list)$/i;
/** Bylines, dates and the headings of the lists after an article: the article text does not carry on across these. */
const AFTER_ARTICLE = /^(related( stories| articles| news)?|recommended|more from\b.*|most (popular|read)|you may also like)$/i;
const BOUNDARY = /^(by\s|published\b|updated\b|posted\b)/i;
const PHOTO_CREDIT = /\[[^\]]*(file|photo|reuters|afp|getty|ap)\b[^\]]*\]$/i;
const MIN_ARTICLE = 80;

const isPageLine = (line: string) => PAGE_LINES.has(line.toLowerCase().replace(/[\s:.]+$/, '')) || PROMPT.test(line);
/** A paragraph ends like a sentence, or is too long to be a menu item or a linked headline. A photo caption is neither. */
const isProse = (line: string) => !PHOTO_CREDIT.test(line) && (line.length >= 100 ? !/^[*•-]\s/.test(line) || /[.!?…។៕]$/.test(line)
  : line.length >= 60 && /[.!?…។៕;][”"'’)\]]*$/.test(line));

/**
 * The article out of a page copied with Select all. The article is where paragraphs follow one another, with at most
 * one short line between them; a teaser or cookie notice stands alone. Inside it, a lone short line stays as a
 * subheading and everything else short goes. Text that does not read like an article comes back as it was.
 */
export function cleanArticleText(pasted: string): string {
  const lines = pasted.replace(/\r\n?/g, '\n')
    .replace(/^\s*list of \d+ items\s*$[\s\S]*?^\s*end of list\s*$/gim, '')
    .replace(/\[([^\]\n]*)\]\(https?:\/\/[^)\s]*\)/g, '$1')
    .split('\n').map(line => line.trim()).filter(line => line && !isPageLine(line));
  const prose = lines.map(isProse);
  const subheading = (i: number) => !prose[i] && !BOUNDARY.test(lines[i]) && !AFTER_ARTICLE.test(lines[i]) && prose[i - 1] && prose[i + 1];
  const continues = (i: number) => prose[i] && (prose[i + 1] || subheading(i + 1));
  let first = lines.findIndex((_, i) => continues(i));
  let last = first < 0 ? -1 : lines.length - 1;
  while (first >= 0 && !(prose[last] && (prose[last - 1] || subheading(last - 1)))) last--;
  if (first < 0) { first = prose.indexOf(true); last = prose.lastIndexOf(true); }
  if (first < 0) return pasted.trim();
  // Teasers under a "Related" heading read like paragraphs, so the article ends there unless most of its text is still to come.
  const size = (from: number, to: number) => lines.slice(from, to + 1).reduce((total, line, i) => total + (prose[from + i] ? line.length : 0), 0);
  const end = lines.findIndex((line, i) => i > first && i < last && AFTER_ARTICLE.test(line) && size(first, i) > size(i, last));
  if (end > 0) last = end - 1;
  const clean = lines.filter((_, i) => i >= first && i <= last && (prose[i] || subheading(i))).join('\n\n');
  return clean.length < MIN_ARTICLE ? pasted.trim() : clean;
}
