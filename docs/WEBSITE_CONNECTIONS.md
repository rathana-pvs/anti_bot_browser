# Website connections and Read more

The Post composer supports Read more for Photo, Reel, and Live. Reel Studio carries
its saved article text, headline, source credit, and goal into the composer. The
article cover starts as the uploaded photo of the reel's first scene, without its text
or crop; a reel with no photo uses its finished image. Imported media can use the same
panel with article text entered there.

Each reel remembers the article it was last published to. Posting the reel again, or
using **Update story**, selects the same website and article, so the update goes to that
article instead of creating another. Its slug, tags, SEO fields and cover are kept, and
Reel Studio's title and text replace the article's only when the story changed. Choose
**Publish a new article** to make a separate one; the reel then remembers that one.

## Connect

1. Open **Settings → Websites** and download the simple example JSON, or use your website connection file.
2. Set `baseUrl` to the website origin. Production connections require HTTPS;
   HTTP is accepted only for localhost development.
3. Click **Upload website JSON**, enter the website credentials, and select **Test connection
   & save**. The website is saved once and is available in every Photo, Reel, and Live draft.
   Settings also offers **Test connection**, **Update login**, and **Remove**.
4. In the Post composer, enable **Read more** and select the saved website.
   **Manage websites** opens the same settings screen, and its changes refresh the picker.

The simple format below is sufficient for Asian Dot. Optional version 2 uses
`http-json-v1` with the API schema embedded in the JSON. It supports JSON login,
HTTP Basic, or a bearer/API token. Version 1 `payload-news-v1` connections continue
to work.

For another website, change its authentication, endpoints, field mappings,
response mappings, and validation rules in `schema`. The website must expose
compatible APIs and honor stable idempotency keys for mutations. JSON cannot
add missing APIs or duplicate prevention to a website.

Use [the complete Asian Dot connection](examples/asian-dot-connection.json) and
[the formal JSON Schema](examples/website-connection.schema.json). The example
below is the simple configuration; version 2 embeds the full API schema.

```json
{
  "schemaVersion": 1,
  "name": "My news website",
  "baseUrl": "https://news.example.com",
  "adapter": "payload-news-v1"
}
```

Optional root `email` (or `username`), `password`, and `api_token` fields can be imported, but
credentials are better entered in the connection form. The backend stores them
under `data/website_connections/` in a directory restricted to its OS user (0700,
SQLite database 0600). These files contain credentials: exclude them from shared
backups. They are never returned in connection listings, saved in composer drafts,
passed to the AI provider, or included in posting jobs. TLS verification stays on;
authenticated requests never follow redirects.

## Prepare, review, and schedule

Choose accounts, enable **Read more**, choose the website, and select an existing
article or prepare a new one. Supply title, excerpt, full
article text, and a cover image. Schema field rules set the limits and additional
required website fields; Asian Dot uses a 160-character excerpt. Category, author, tags, source credit, slug,
cover alt text, SEO title, and SEO description are available in article details.

**Publish article & create links** publishes the article immediately,
even when the Facebook work will be scheduled later. There is no website draft
mode. New articles are not automatically marked Breaking or Featured. Existing
articles retain those flags. Existing rich-text formatting is retained if the
plain body text has not changed; edited body text is converted into paragraphs.

**Unique caption for each account** and **Read more** are separate options, and each can be
used without the other. **Generate captions** never contacts a website. It is off by default,
uses the Gemini key/model configured in Settings, and replaces the supplied caption. The AI reads
the article text when the post has one (from Reel Studio or the article form), otherwise the post's
caption, up to the first 4,000 characters. Generating again for the same text keeps the existing
captions, including your edits, and only asks the AI about newly added accounts. Without Read more,
the post keeps its own first comment. With both on, generate the captions first, then
**Publish article & create links**, so captions are reviewed before anything is published.
Each generated hook has 10–30 whitespace-separated words, no links, and must pass
length, numeric-claim, and similarity checks. Caption generation is text-only.
Review factual accuracy and edit or regenerate individual hooks before scheduling.
With generation disabled, each destination uses the supplied caption.

Every account receives its own persisted tracking link. The review shows the
published article URL, each account's editable caption, and its comment URL.
Changes to article, media, source caption, selected accounts, website, or caption
mode invalidate preparation. Captions edited in the review do not change links.

Photo/Reel use the first-comment path. Live uses the existing comment-after-end
path; the UI explains this timing. Phone Photo/Reel execution and phone comment
follow-ups use the same saved destination-specific values.

## Recovery and ownership

The local preparation journal retains request IDs, generated captions, confirmed
website publication, and completed links after partial failures. Retrying a request
reconciles the same website operations. Article publication keys are independent
of selected Facebook accounts, so adding accounts does not create another copy
of an identical new article. The website stores publication receipts and unique
request keys for uploaded covers and comment links.

Posting jobs copy captions and links into per-account execution snapshots. No AI
or link generation runs during execution or retry. Repeated submission of the same
Read more batch returns the saved batch; changed submissions must be prepared as
new work. Publication uncertainty and comment-only recovery retain their existing
rules. Appending prepared Read more content to an existing batch is not supported;
create a new batch instead.

The desktop backend calls the website over HTTPS. It remains authenticated and
loopback-only. This does not extend the cloud worker protocol or add a second job
queue. `main-server` is unchanged; remote dashboard authoring of these preparations
is not implemented.

## Website installation

Deploy the sibling website changes and apply its `20261104_website_studio` migration
before connecting an existing production database. Start the desktop backend and
frontend with these changes. The website adds an authenticated `/api/studio` API
and preserves custom SEO values. Tracked article URLs use the main article URL as
their canonical and reject keys belonging to another article.

No production website or Facebook publishing is needed to validate this feature.
The website's `test/studio.integration.ts` uses only a disposable PostgreSQL server
on port 55439, with temporary media storage. See the website integration guide.

## Version 2 API schema

The connection file includes both a `$schema` reference for editor validation and
an embedded `schema` object used at runtime. The adjacent formal JSON Schema is
optional for importing; the embedded API schema travels in the single JSON file.

- `authentication`: `json-login` (custom login body and token response path),
  `basic` (username/password), or `bearer` (root `api_token`, configurable header
  and prefix). Use `{{credentials.email}}`, `{{credentials.username}}`, and
  `{{credentials.password}}` only inside the login request.
- `fields`: standard article fields and custom entries such as
  `custom_fields.region`, with label, required flag, type, default, and maximum
  length. Custom string, number, and boolean controls appear in the composer.
  `requireAny` groups describe alternatives, such as a cover upload or an existing cover ID.
- `operations`: required `test`, `publish`, `verify`, and `commentLink`; optional
  `upload`, `update`, and `catalog`. Tests, verification, and catalogs use GET.
  Mutations use POST, PUT, or PATCH. Paths are relative to the configured origin.
- Each operation describes `path`, `method`, `query`, `body`, `encoding`
  (`json`, `form`, or `multipart`), and `response` mappings. Multipart can use a
  `payloadField` for JSON inside one form field and a configurable `fileField`.
  A separate upload operation maps its response ID into `article.cover_id`.
- Whole-value references such as `"{{article.title}}"`, `"{{article}}"`,
  `"{{article.custom_fields.region}}"`, and `"{{request_id}}"` preserve data types.
  URL path substitutions are encoded. Response mappings use dotted property paths
  such as `data.id` and `data.permalink`; array indices are supported. An optional
  `urlTemplate` can construct a public URL from `baseUrl` and `response` fields.
- `contentFormat`: plain text, escaped HTML paragraphs, or a Lexical document.
- `publishedValue` and `publicationStatusField`: force the website's published
  value into the correct request field. Publication responses must confirm it.
  `verifyValue` is compared strictly; the string `"false"` never confirms `true`.
- `idempotentMutations: true`: declares a website-side requirement, not a client
  implementation of it. Every mutation sends a stable `Idempotency-Key` header
  (or configured `idempotencyHeader`); map `request_id` into the body as well if
  required. Separate cover uploads receive their own stable key. API timeouts
  reconcile the same keys. The website must implement this contract.

Public article and tracking URLs must remain on the configured website origin.
Templates are data substitutions, never JavaScript/Python or executable code.
Reimporting a changed API schema blocks unfinished preparation until the prior
schema is restored for reconciliation. Stored credentials and the worker protocol
remain local and unchanged.
