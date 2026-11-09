-- Linda 0.4.8.3 - lokální pozvánky ke sdílení projektu.
-- Aditivní migrace, nemaže ani neupravuje existující pracovní data.
CREATE TABLE IF NOT EXISTS project_invitations (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    project_id BIGINT NOT NULL,
    email VARCHAR(190) NOT NULL,
    role VARCHAR(40) NOT NULL DEFAULT 'member',
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    invited_by BIGINT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_linda_project_invite(project_id,email),
    KEY ix_linda_project_invite_email(email,status),
    KEY ix_linda_project_invite_project(project_id,status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
