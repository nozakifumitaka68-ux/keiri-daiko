-- ===================================
-- 経理代行システム Supabase DB スキーマ
-- ===================================
-- Supabaseのプロジェクトを作成後、SQL Editorで全文を貼り付けて実行してください。
-- これでテーブル + Storage + RLSポリシーが一括セットアップされます。

-- ===================================
-- 1. journals(仕訳)テーブル
-- ===================================
CREATE TABLE IF NOT EXISTS public.journals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id TEXT NOT NULL,
    transaction_date DATE,
    vendor TEXT,
    vendor_registration_number TEXT,
    debit TEXT,
    credit TEXT,
    amount BIGINT,
    tax_amount BIGINT,
    tax_rate INTEGER,
    tax_category TEXT,
    description TEXT,
    payment_method_hint TEXT,
    people_count INTEGER,
    per_person_amount BIGINT,
    match_status TEXT DEFAULT 'cash_pending',
    matched_card_statement_id UUID,
    needs_review BOOLEAN DEFAULT false,
    review_reasons JSONB,
    confidence DOUBLE PRECISION,
    source_file TEXT,
    file_hash TEXT,
    receipt_path TEXT,
    receipt_filename TEXT,
    ocr_raw JSONB,
    settlement_info JSONB,
    -- 削除管理(ソフトデリート)
    is_deleted BOOLEAN DEFAULT false,
    deleted_at TIMESTAMPTZ,
    delete_reason TEXT,
    restored_at TIMESTAMPTZ,
    -- マネフォ登録結果
    mf_registration JSONB,
    mf_mode TEXT,
    -- タイムスタンプ
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    registered_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_journals_client_id ON public.journals(client_id);
CREATE INDEX IF NOT EXISTS idx_journals_match_status ON public.journals(match_status);
CREATE INDEX IF NOT EXISTS idx_journals_file_hash ON public.journals(file_hash);
CREATE INDEX IF NOT EXISTS idx_journals_is_deleted ON public.journals(is_deleted);
CREATE INDEX IF NOT EXISTS idx_journals_transaction_date ON public.journals(transaction_date);

-- ===================================
-- 2. card_statements(カード明細)テーブル
-- ===================================
CREATE TABLE IF NOT EXISTS public.card_statements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id TEXT NOT NULL,
    card_name TEXT,
    usage_date DATE,
    posting_date DATE,
    vendor_raw TEXT,
    amount BIGINT,
    memo TEXT,
    raw_row JSONB,
    -- 突合状態
    match_status TEXT DEFAULT 'unmatched',
    matched_journal_id UUID,
    -- 銀行引落決済状態
    settlement_status TEXT,
    -- 削除管理
    is_deleted BOOLEAN DEFAULT false,
    deleted_at TIMESTAMPTZ,
    delete_reason TEXT,
    restored_at TIMESTAMPTZ,
    -- タイムスタンプ
    imported_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_card_client_id ON public.card_statements(client_id);
CREATE INDEX IF NOT EXISTS idx_card_match_status ON public.card_statements(match_status);
CREATE INDEX IF NOT EXISTS idx_card_settlement_status ON public.card_statements(settlement_status);
CREATE INDEX IF NOT EXISTS idx_card_is_deleted ON public.card_statements(is_deleted);

-- ===================================
-- 3. bank_statements(銀行明細)テーブル
-- ===================================
CREATE TABLE IF NOT EXISTS public.bank_statements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id TEXT NOT NULL,
    account_name TEXT,
    transaction_date DATE,
    description TEXT,
    amount BIGINT,
    balance BIGINT,
    raw_row JSONB,
    -- 突合状態
    match_status TEXT DEFAULT 'unmatched',
    matched_card_statement_ids JSONB DEFAULT '[]'::JSONB,
    settlement_journal_id UUID,
    -- 削除管理
    is_deleted BOOLEAN DEFAULT false,
    deleted_at TIMESTAMPTZ,
    delete_reason TEXT,
    restored_at TIMESTAMPTZ,
    -- タイムスタンプ
    imported_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bank_client_id ON public.bank_statements(client_id);
CREATE INDEX IF NOT EXISTS idx_bank_match_status ON public.bank_statements(match_status);
CREATE INDEX IF NOT EXISTS idx_bank_is_deleted ON public.bank_statements(is_deleted);

-- ===================================
-- 4. Row Level Security(RLS)設定
-- 全テーブルでデフォルト拒否 → 認証されたユーザーのみアクセス可
-- ===================================
ALTER TABLE public.journals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.card_statements ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bank_statements ENABLE ROW LEVEL SECURITY;

-- 簡易ポリシー: anon キーで全アクセス許可(デモ用)
-- 本番運用時はクライアントID単位で絞る
DROP POLICY IF EXISTS "anon_all_journals" ON public.journals;
CREATE POLICY "anon_all_journals" ON public.journals
    FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_all_card_statements" ON public.card_statements;
CREATE POLICY "anon_all_card_statements" ON public.card_statements
    FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_all_bank_statements" ON public.bank_statements;
CREATE POLICY "anon_all_bank_statements" ON public.bank_statements
    FOR ALL USING (true) WITH CHECK (true);

-- ===================================
-- 5. updated_at 自動更新トリガー
-- ===================================
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS journals_updated_at ON public.journals;
CREATE TRIGGER journals_updated_at
    BEFORE UPDATE ON public.journals
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS card_statements_updated_at ON public.card_statements;
CREATE TRIGGER card_statements_updated_at
    BEFORE UPDATE ON public.card_statements
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

DROP TRIGGER IF EXISTS bank_statements_updated_at ON public.bank_statements;
CREATE TRIGGER bank_statements_updated_at
    BEFORE UPDATE ON public.bank_statements
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- ===================================
-- 6. Storage バケット: receipts
-- 領収書画像を保管するためのバケット
-- ※ 別途 Supabase ダッシュボードの Storage タブから手動作成も可能
-- ===================================
-- Note: storage.buckets はマネージドテーブル
INSERT INTO storage.buckets (id, name, public, file_size_limit)
VALUES ('receipts', 'receipts', false, 10485760)  -- 10MB上限
ON CONFLICT (id) DO NOTHING;

-- バケットへのアクセスポリシー(anon キーで読み書き可)
DROP POLICY IF EXISTS "anon_read_receipts" ON storage.objects;
CREATE POLICY "anon_read_receipts" ON storage.objects
    FOR SELECT USING (bucket_id = 'receipts');

DROP POLICY IF EXISTS "anon_insert_receipts" ON storage.objects;
CREATE POLICY "anon_insert_receipts" ON storage.objects
    FOR INSERT WITH CHECK (bucket_id = 'receipts');

DROP POLICY IF EXISTS "anon_update_receipts" ON storage.objects;
CREATE POLICY "anon_update_receipts" ON storage.objects
    FOR UPDATE USING (bucket_id = 'receipts');

DROP POLICY IF EXISTS "anon_delete_receipts" ON storage.objects;
CREATE POLICY "anon_delete_receipts" ON storage.objects
    FOR DELETE USING (bucket_id = 'receipts');

-- ===================================
-- 完了
-- ===================================
-- 確認SQL:
-- SELECT count(*) FROM public.journals;
-- SELECT count(*) FROM public.card_statements;
-- SELECT count(*) FROM public.bank_statements;
-- SELECT * FROM storage.buckets WHERE id = 'receipts';
