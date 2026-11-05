import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = ts.transpileModule(readFileSync(new URL('../src/services/articleText.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
}).outputText;
const { cleanArticleText } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

const one = 'As the midterm votes trickled in on Tuesday, Republicans began pointing fingers at the administration.';
const two = 'About six in 10 voters surveyed said that going to war was the wrong decision.';
const three = '“There are very few issues on which we will see such a stark contrast,” Payne said.';

test('a recommended stories block, its links and adverts are removed', () => {
  const pasted = [one, 'Recommended Stories', 'list of 4 items', '',
    '* list 1 of 4[Security officer killed in Amman](https://www.aljazeera.com/news/a)',
    '* list 2 of 4[Workers strike across France](https://www.aljazeera.com/news/b)', '', 'end of list',
    two, 'Advertisement', three].join('\n');
  assert.equal(cleanArticleText(pasted), [one, two, three].join('\n\n'));
});

test('the plain text form of the same block is removed', () => {
  const pasted = [one, 'Recommended Stories', 'list of 2 items', 'list 1 of 2', 'Security officer killed in Amman',
    'list 2 of 2', 'Workers strike across France', 'end of list', two].join('\n');
  assert.equal(cleanArticleText(pasted), [one, two].join('\n\n'));
});

test('a link inside a paragraph keeps its words', () => {
  assert.equal(cleanArticleText(`He [won his contest](https://example.com/a?b=1) despite a vigorous campaign by the Republicans and their allies in the state.`),
    'He won his contest despite a vigorous campaign by the Republicans and their allies in the state.');
});

test('menus before and after the article go, a lone subheading stays', () => {
  const pasted = ['News', 'Sport', 'Skip to main content', 'Published on 5 Nov', one, 'Why voters moved', two, 'About', 'Contact us', 'Terms'].join('\n');
  assert.equal(cleanArticleText(pasted), [one, 'Why voters moved', two].join('\n\n'));
});

test('a run of short lines inside the article goes', () => {
  assert.equal(cleanArticleText([one, 'Headline one', 'Headline two', two].join('\n')), [one, two].join('\n\n'));
});

test('a whole page keeps only the article between its menus, video list, related stories and footer', () => {
  const para = word => `${word} said the result would change how the party talks about its foreign policy before the next election.`;
  const page = form => [
    'Advertisement', 'Navigation menu', form('News'), form('Why do the midterms matter?'), 'ANALYSIS',
    'Could the midterms change Washington?', 'The midterms showed that the issue can win votes, but changing the policy may be harder to do.',
    'Listen (9 mins)', 'Save', 'Share', 'The president and the prime minister shake hands following a meeting at the club in Florida in December [File: Jonathan Ernst/Reuters]',
    'By Jane Writer', 'Published On 5 Nov 2026',
    para('Alice'), para('Bob'), 'Advertisement', para('Carol'),
    'Play Video', '2:26', form('Now Playing'), '02:26', 'Bomb threats disrupt flights across Europe', form('01:59'), 'What can Democrats do in Congress under President Trump?', '01:04',
    para('Dan'), 'Get instant alerts and updates based on your interests. Be the first to know when big stories happen.', 'Yes, keep me updated', para('Erin'),
    'Rebuilding support', para('Frank'), para('Grace'),
    'Related', form('Will the war of attrition take hold across the country this winter?'), 'The fighters suffer losses but also inflict pain on the government and its allies.', 'Published On 5 Nov 2026',
    form('What will the results of the vote mean for these three conflicts over the next year?'), 'Despite wins, the party lacks the majority needed to overturn a veto and push through a new law this year.', 'Published On 5 Nov 2026',
    'More from News', form('Most popular story of the day'), 'We use cookies and other tracking technologies to deliver and personalize content and ads, and enable social media sharing. You can choose to customize your preferences.',
    'Cookie preferencesAllow all',
  ].join('\n');
  const expected = [para('Alice'), para('Bob'), para('Carol'), para('Dan'), para('Erin'), 'Rebuilding support', para('Frank'), para('Grace')].join('\n\n');
  assert.equal(cleanArticleText(page(text => `* [${text}](https://example.com/a)`)), expected);
  assert.equal(cleanArticleText(page(text => text)), expected);
});

test('Khmer sentences count as article text', () => {
  const khmer = 'រដ្ឋាភិបាលបានប្រកាសថា ការងារសាងសង់ស្ពានថ្មីនឹងចាប់ផ្តើមនៅខែក្រោយ ហើយត្រូវចំណាយពេលពីរឆ្នាំ។';
  assert.equal(cleanArticleText(['Menu', khmer, 'Advertisement', khmer, 'Footer'].join('\n')), [khmer, khmer].join('\n\n'));
});

test('text that does not read like an article is left alone', () => {
  for (const text of ['https://example.com/story', 'a short note', 'Line one\nLine two']) assert.equal(cleanArticleText(text), text);
});
