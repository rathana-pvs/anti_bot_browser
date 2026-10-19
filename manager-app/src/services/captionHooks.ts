export interface HookApproval {
  approval_id: string;
  source_revision: string;
  final_caption: string;
}
export interface HookDraft {
  enabled: boolean;
  description?: string;
  audience?: string;
  language?: string;
  tone?: string;
  avoid?: string;
  candidates?: { id: string; hook: string }[];
  generationId?: string;
  sourceFingerprint?: string;
  selected?: string;
  approval?: HookApproval;
}

export function hookFingerprint(caption: string, media: string, type: string, draft: HookDraft): string {
  return JSON.stringify([caption, media, type, draft.description || '', draft.audience || '',
    draft.language || '', draft.tone || '', draft.avoid || '']);
}

export function hookIsApproved(caption: string, media: string, type: string, draft?: HookDraft): boolean {
  return !draft?.enabled || (!!draft.approval && draft.sourceFingerprint === hookFingerprint(caption, media, type, draft)
    && draft.approval.final_caption === (draft.selected || '').trim() + (caption ? '\n\n' + caption : ''));
}
