-- Zadania analizy, paczki (batch) i zatwierdzone formularze.
-- Zasada: w bazie wyłącznie dane pochodne (metadane, SHA-256, wygenerowany i zatwierdzony
-- JSON) — nigdy treść przesłanego pliku.

CREATE TABLE batches (
    id          text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    owner       text NOT NULL,
    total_files integer NOT NULL CHECK (total_files >= 0),
    -- pliki odrzucone przy walidacji: [{filename, code, message}]
    rejected    jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(rejected) = 'array'),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX batches_owner_created_idx ON batches (owner, created_at DESC);

CREATE TABLE jobs (
    id            text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    owner         text NOT NULL,
    batch_id      text REFERENCES batches (id) ON DELETE CASCADE,
    filename      text NOT NULL,
    extension     text NOT NULL,
    mime_type     text NOT NULL,
    size_bytes    bigint NOT NULL CHECK (size_bytes > 0),
    sha256        text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    status        text NOT NULL DEFAULT 'QUEUED'
                  CHECK (status IN ('QUEUED', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED')),
    attempts      integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code    text,
    error_message text,
    -- wygenerowany wynik {form, report}; tylko dla SUCCEEDED
    result        jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jobs_result_only_when_succeeded CHECK ((status = 'SUCCEEDED') = (result IS NOT NULL)),
    CONSTRAINT jobs_error_only_when_failed CHECK ((status = 'FAILED') = (error_code IS NOT NULL)),
    CONSTRAINT jobs_finished_at_when_finished
        CHECK ((status IN ('SUCCEEDED', 'FAILED')) = (finished_at IS NOT NULL))
);

CREATE INDEX jobs_owner_created_idx ON jobs (owner, created_at DESC);
CREATE INDEX jobs_batch_idx ON jobs (batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX jobs_unfinished_idx ON jobs (created_at) WHERE status IN ('QUEUED', 'IN_PROGRESS');
CREATE INDEX jobs_sha256_idx ON jobs (sha256);

-- Zatwierdzone formularze przeżywają retencję zadań (katalog wzorców, pętla zwrotna).
CREATE TABLE approved_forms (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id      text UNIQUE REFERENCES jobs (id) ON DELETE SET NULL,
    owner       text NOT NULL,
    sha256      text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    title       text NOT NULL,
    form        jsonb NOT NULL CHECK (jsonb_typeof(form) = 'object'),
    approved_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX approved_forms_sha256_idx ON approved_forms (sha256);
