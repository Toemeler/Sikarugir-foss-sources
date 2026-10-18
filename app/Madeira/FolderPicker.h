#pragma once
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

/// Settings › External drive: a document picker that chooses one folder and
/// opens it in place (security-scoped, not copied). See FolderPicker.m for why
/// SwiftUI's fileImporter cannot do this inside LiveContainer.
UIDocumentPickerViewController *madeira_folder_picker(id<UIDocumentPickerDelegate> delegate);

/// 1 when the picker came from UIKit's own initializer behind LiveContainer's
/// hook, 0 for the plain initializer (no hook found). For the log.
int madeira_folder_picker_bypassed(void);

NS_ASSUME_NONNULL_END
