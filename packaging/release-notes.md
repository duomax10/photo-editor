Portable build for Windows. No installer, and no Python needed.

## Setup

1. Download the `.zip` below.
2. Right-click it and choose **Extract All**. Extract it *before* running it —
   opening the program from inside the ZIP will not work.
3. Open the extracted folder and double-click **PhotoTimestampEditor.exe**.
4. Windows may show *"Windows protected your PC"*. That box appears for any app
   without a paid code-signing certificate — click **More info**, then
   **Run anyway**. It only asks the first time.

## Using it

1. Click **Browse** and pick the folder your photos are in.
2. Set the **Hours** box — for example `-5` if the camera was five hours ahead.
3. Check the preview table. It shows every photo's current and new date before
   anything is changed.
4. Click **Apply to all photos**. **Undo last change** reverses it exactly.

## Notes

Photos are never re-saved or re-compressed — only the date characters inside
each file are overwritten in place — so there is no loss of quality, and no file
is copied, moved or deleted.

Works on JPEG, HEIC/HEIF, TIFF, PNG and raw files (CR2, NEF, ARW, DNG, ORF,
RW2, PEF, SRW, RAF). Only the folder you pick is processed; subfolders are left
alone.

Settings and undo history stay in the `data` folder next to the program, so
nothing is written to your user profile or the registry. To uninstall, delete
the folder.

`READ-ME-FIRST.txt` inside the ZIP repeats all of this.
