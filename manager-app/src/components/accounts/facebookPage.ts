/** The page name and follower count warming visits read from each account's own profile. */
export interface FacebookPageSnapshot {
  /** Missing when the Page name could not be read (a script shown as boxes). */
  name?: string;
  kind?: 'page' | 'personal';
  followers?: number;
  seen_at?: string;
  followers_seen_at?: string;
}

/** 950 → "950", 12_345 → "12.3K", 1_500_000 → "1.5M". */
export function shortCount(value: number): string {
  const units: [number, string][] = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
  for (const [size, unit] of units) {
    if (value >= size) {
      const scaled = value / size;
      const text = scaled >= 100 ? Math.floor(scaled).toString() : (Math.floor(scaled * 10) / 10).toString();
      return `${text}${unit}`;
    }
  }
  return String(value);
}

export function seenAgo(iso: string | undefined, now: number = Date.now()): string {
  const at = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(at)) return '';
  const minutes = Math.max(0, Math.round((now - at) / 60_000));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export interface PageCells {
  page: string;
  pageHint: string;
  followers: string;
  followersHint: string;
  /** For sorting: the name, and the follower count (null when unknown). */
  sortName: string | null;
  sortFollowers: number | null;
}

const NOT_YET = 'Read during the next warming visit that opens this account’s profile.';

export function pageCells(snapshot: FacebookPageSnapshot | null | undefined, phone: boolean, now: number = Date.now()): PageCells {
  if (!snapshot?.name && typeof snapshot?.followers !== 'number') {
    const hint = phone ? 'Not read on phone accounts yet.' : NOT_YET;
    return { page: '—', pageHint: hint, followers: '—', followersHint: hint, sortName: null, sortFollowers: null };
  }
  const seen = seenAgo(snapshot.seen_at, now);
  const kind = snapshot.kind === 'page' ? 'Page' : snapshot.kind === 'personal' ? 'Personal profile' : 'Profile';
  const pageHint = snapshot.name ? `${kind}: ${snapshot.name}${seen ? ` · seen ${seen} while warming` : ''}`
    : 'Facebook shows this Page’s name in a script the browser can’t display, so only the follower count was read.';
  const followers = typeof snapshot.followers === 'number' ? snapshot.followers : null;
  const followersSeen = seenAgo(snapshot.followers_seen_at ?? snapshot.seen_at, now);
  return {
    page: !snapshot.name ? 'Name not readable' : snapshot.kind === 'personal' ? `${snapshot.name} (personal)` : snapshot.name,
    pageHint,
    followers: followers === null ? '—' : shortCount(followers),
    followersHint: followers === null ? 'Facebook did not show a follower count on the last visit.'
      : `${followers.toLocaleString('en-US')} followers${followersSeen ? ` · seen ${followersSeen} while warming` : ''}`,
    sortName: snapshot.name ?? null,
    sortFollowers: followers,
  };
}
