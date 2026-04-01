with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Find the manual form section
start_idx = content.find('manual_card_number')
if start_idx > 0:
    # Get context around it
    ctx_start = max(0, start_idx - 500)
    ctx_end = min(len(content), start_idx + 500)
    print("Context around manual_card_number:")
    print(content[ctx_start:ctx_end])
    print("\n" + "="*50 + "\n")

# Check if manual_phone exists
phone_idx = content.find('manual_phone')
if phone_idx > 0:
    ctx_start = max(0, phone_idx - 300)
    ctx_end = min(len(content), phone_idx + 300)
    print("Context around manual_phone:")
    print(content[ctx_start:ctx_end])
else:
    print("manual_phone NOT FOUND!")
