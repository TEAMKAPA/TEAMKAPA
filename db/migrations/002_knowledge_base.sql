-- Baza wiedzy (RAG): katalog typów dokumentów i przykłady formularzy z wektorami
-- (intfloat/multilingual-e5-small, 384 wymiary, podobieństwo kosinusowe).
-- W bazie wyłącznie dane pochodne: wektor, skrót SHA-256 i konfiguracja COI —
-- treść dokumentów użytkowników nie jest zapisywana.

CREATE EXTENSION IF NOT EXISTS vector;

-- Typy dokumentów: katalog (formularze od organizatora) i typy utworzone z zatwierdzeń.
CREATE TABLE document_types (
    code       text PRIMARY KEY CHECK (code ~ '^[a-z0-9_]{1,100}$'),
    title      text NOT NULL CHECK (length(title) BETWEEN 1 AND 255),
    source     text NOT NULL CHECK (source IN ('catalog', 'approved')),
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Przykłady: sygnatura katalogowa typu albo dokument zatwierdzony przez urzędnika
-- (pętla zwrotna). form = konfiguracja COI — wzorzec dla modelu przy podobnym dokumencie.
CREATE TABLE knowledge_examples (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doc_type         text NOT NULL REFERENCES document_types (code) ON DELETE CASCADE,
    source           text NOT NULL CHECK (source IN ('catalog', 'approved')),
    sha256           text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    embedding_model  text NOT NULL,
    embedding        vector(384) NOT NULL,
    form             jsonb CHECK (form IS NULL OR jsonb_typeof(form) = 'object'),
    approved_form_id bigint UNIQUE REFERENCES approved_forms (id) ON DELETE CASCADE,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK ((source = 'approved') = (approved_form_id IS NOT NULL))
);

-- Jeden wpis katalogowy na typ i model (aktualizowany przy zmianie sygnatury).
CREATE UNIQUE INDEX knowledge_examples_catalog_idx
    ON knowledge_examples (doc_type, embedding_model) WHERE source = 'catalog';
CREATE INDEX knowledge_examples_embedding_idx
    ON knowledge_examples USING hnsw (embedding vector_cosine_ops);
CREATE INDEX knowledge_examples_sha256_idx ON knowledge_examples (sha256);

-- Wektor dokumentu z analizy — przy zatwierdzeniu trafia do bazy wiedzy (bez ponownego
-- liczenia w API, które nie ma modelu). Usuwany razem z zadaniem (retencja).
ALTER TABLE jobs
    ADD COLUMN embedding       vector(384),
    ADD COLUMN embedding_model text;
