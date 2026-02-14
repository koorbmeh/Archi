import os
from datetime import datetime

def categorize_files(workspace_path):
    file_types = {
        'documents': ['.txt', '.pdf', '.docx', '.odt'],
        'images': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg'],
        'code': ['.py', '.js', '.java', '.cpp', '.h', '.rs', '.go', '.swift', '.dart', '.ts'],
        'videos': ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv'],
        'other': []
    }

    files = os.listdir(workspace_path)
    categorized_files = {key: [] for key in file_types}

    for file in files:
        if os.path.isfile(os.path.join(workspace_path, file)):
            ext = os.path.splitext(file)[1].lower()
            matched = False
            for category, extensions in file_types.items():
                if ext in extensions:
                    categorized_files[category].append(file)
                    matched = True
                    break
            if not matched:
                categorized_files['other'].append(file)

    return categorized_files

if __name__ == '__main__':
    workspace_path = 'workspace/'
    result = categorize_files(workspace_path)
    print('Categorized Files:')
    for category, files in result.items():
        print(f"{category.upper()}: {len(files)} files")