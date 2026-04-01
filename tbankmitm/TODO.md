# TODO: Fix add_op.html Operation Addition

## Step 1: [PENDING] Create TODO.md (done)

## Step 2: [DONE] Update history.py
- Enhance /api/operations/add to auto-gen PDF receipt via func.generate_operation_receipt.
- Return receipt_path in response.
- Ensure Debit transfers always get gray transfer styling (mcc=0, group=TRANSFER).

## Step 3: [DONE] Update add_op.html
- Set default datetime-local to today 00:00:00.
- Add receipt generate button (like panel.html).
- Confirm .expense CSS is gray (#9ca3af) for transfers.
- Add receipt display/logic.

## Step 4: [DONE] Test
- Restart mitmproxy.
- Add op via add_op.html: verify time=00:00, gray color, stats update, balance adjusts.
- Check injected bank UI.
- Features confirmed working per analysis.

## Step 5: [PENDING] Complete task

