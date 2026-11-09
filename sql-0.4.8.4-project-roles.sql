-- Linda 0.4.8.4
ALTER TABLE project_members ADD COLUMN IF NOT EXISTS relationship VARCHAR(24) NOT NULL DEFAULT 'member';
ALTER TABLE project_invitations ADD COLUMN IF NOT EXISTS relationship VARCHAR(24) NOT NULL DEFAULT 'member';
-- Legacy permission labels are normalized by application code; no destructive rewrite is required.
