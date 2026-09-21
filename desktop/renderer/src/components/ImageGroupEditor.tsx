import { useState } from "react";

export interface ImageFile {
  path?: string;
  name: string;
  storedName?: string;
}

export interface ImageGroupEditorProps {
  files: ImageFile[];
  disabled?: boolean;
  onChange: (files: ImageFile[]) => void;
}

export function removeImage(files: ImageFile[], index: number): ImageFile[] {
  if (index < 0 || index >= files.length) return files;
  return files.filter((_, fileIndex) => fileIndex !== index);
}

export function moveImage(files: ImageFile[], index: number, offset: -1 | 1): ImageFile[] {
  const target = index + offset;
  if (index < 0 || index >= files.length || target < 0 || target >= files.length) return files;

  const next = [...files];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function ImageGroupEditor({ files, disabled = false, onChange }: ImageGroupEditorProps) {
  const [movingFiles, setMovingFiles] = useState<Record<string, "up" | "down">>({});

  const handleMove = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (index < 0 || index >= files.length || target < 0 || target >= files.length) return;
    const current = files[index];
    const other = files[target];
    const currentKey = current.path ?? current.name;
    const otherKey = other.path ?? other.name;

    setMovingFiles({
      [currentKey]: offset === -1 ? "up" : "down",
      [otherKey]: offset === -1 ? "down" : "up",
    });
    onChange(moveImage(files, index, offset));
    setTimeout(() => {
      setMovingFiles({});
    }, 200);
  };

  return (
    <section className="setup-section image-group" aria-labelledby="image-group-heading">
      <div className="section-heading">
        <div>
          <p className="section-label" id="image-group-heading">Image order</p>
        </div>
        <span className="section-count" aria-label={`${files.length} images`}>
          {String(files.length).padStart(2, "0")}
        </span>
      </div>

      <ol className="image-list">
        {files.map((file, index) => {
          const fileKey = file.path ?? file.name;
          const moveDir = movingFiles[fileKey];
          return (
            <li
              className="image-row"
              key={fileKey}
              data-image-move={moveDir}
            >
              <span className="image-row-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
              <span className="image-row-name" title={file.path}>{file.name}</span>
              <div className="image-row-actions">
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => handleMove(index, -1)}
                  disabled={disabled || index === 0}
                  aria-label={`Move ${file.name} up`}
                  title="Move up"
                >
                  ↑
                </button>
                <button
                  type="button"
                  className="icon-button"
                  onClick={() => handleMove(index, 1)}
                  disabled={disabled || index === files.length - 1}
                  aria-label={`Move ${file.name} down`}
                  title="Move down"
                >
                  ↓
                </button>
                <button
                  type="button"
                  className="image-remove-button"
                  onClick={() => onChange(removeImage(files, index))}
                  disabled={disabled}
                  aria-label={`Remove ${file.name}`}
                >
                  Remove
                </button>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
