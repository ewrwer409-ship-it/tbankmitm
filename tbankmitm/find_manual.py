with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Find manual form section
idx = content.find('manual_phone')
if idx > 0:
    # Print context around manual_phone
    start = max(0, idx - 200)
    end = min(len(content), idx + 400)
    print(content[start:end])
else:
    print('manual_phone not found')
    # Look for manual_direction or manual_amount
    for field in ['manual_direction', 'manual_amount', 'manual_title', 'manual_subtitle']:
        idx = content.find(field)
        if idx > 0:
            print(f'{field} found at position {idx}')
            start = max(0, idx - 100)
            end = min(len(content), idx + 200)
            print(content[start:end])
            print('---')
