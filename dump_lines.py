with open('panel.html', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

# Print lines 540-580 (around manual form)
for i in range(539, min(580, len(lines))):
    print(f"{i+1}: {lines[i]}", end='')
