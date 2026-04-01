with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

# Find lines around manual_phone and manual_card_number
for i, line in enumerate(lines):
    if 'manual_phone' in line or 'manual_card_number' in line or 'manual_comment' in line:
        print(f"{i+1}: {line.rstrip()}")
