//
//  FolderPicker.m
//  Madeira
//
//  The folder picker for Settings › External drive (ExternalDrive.swift).
//
//  Measured on the iPad (build 56, 2026-10-17): inside LiveContainer, SwiftUI's
//  .fileImporter(allowedContentTypes: [.folder]) showed the drive and its
//  folders, but its Open button did nothing, at the drive's top level and
//  inside a folder alike, by pointer and by touch. LiveContainer's TweakLoader
//  (TweakLoader/DocumentPicker.m) swizzles
//  -[UIDocumentPickerViewController initForOpeningContentTypes:asCopy:] and
//  replaces every app's types with @[UTTypeItem, UTTypeFolder]; a picker for
//  items and folders opens files and navigates into folders, so a folder can
//  never be chosen. (With its "fix file picker" option it also forces asCopy
//  and multiple selection; Madeira's entry has that option off.)
//
//  LiveContainer swizzles with method_exchangeImplementations, so after the
//  exchange its selector hook_initForOpeningContentTypes:asCopy: runs UIKit's
//  original initializer. This file calls that selector, with [UTTypeFolder]
//  and asCopy NO, only when its implementation lives in UIKit (the exchange
//  happened); otherwise the hook was never installed, and calling it would
//  reach LiveContainer's own method, so the plain initializer is used.
//

#import "FolderPicker.h"
#import <UniformTypeIdentifiers/UniformTypeIdentifiers.h>
#import <objc/message.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <string.h>

static int madeira_picker_bypassed;

/// The implementation behind `selector` on UIDocumentPickerViewController when
/// it is UIKit's own (its image is UIKitCore or UIKit), else NULL.
static IMP madeira_uikit_imp(SEL selector)
{
    Method method = class_getInstanceMethod(UIDocumentPickerViewController.class, selector);
    Dl_info info;
    IMP imp;

    if (!method || !(imp = method_getImplementation(method))) return NULL;
    if (!dladdr((const void *)imp, &info) || !info.dli_fname) return NULL;
    return (strstr(info.dli_fname, "/UIKitCore.framework/") || strstr(info.dli_fname, "/UIKit.framework/")) ? imp : NULL;
}

UIDocumentPickerViewController *madeira_folder_picker(id<UIDocumentPickerDelegate> delegate)
{
    NSArray<UTType *> *types = @[UTTypeFolder];
    SEL hooked = NSSelectorFromString(@"hook_initForOpeningContentTypes:asCopy:");
    IMP original = madeira_uikit_imp(hooked);
    UIDocumentPickerViewController *picker;

    if (original)
    {
        /* An initializer: it consumes the allocated object and returns a retained one (ARC). */
        typedef UIDocumentPickerViewController *(*init_fn)(__attribute__((ns_consumed)) id, SEL, NSArray<UTType *> *, BOOL)
            __attribute__((ns_returns_retained));
        picker = ((init_fn)original)([UIDocumentPickerViewController alloc], @selector(initForOpeningContentTypes:asCopy:), types, NO);
        madeira_picker_bypassed = 1;
    }
    else
    {
        picker = [[UIDocumentPickerViewController alloc] initForOpeningContentTypes:types asCopy:NO];
        madeira_picker_bypassed = 0;
    }
    picker.delegate = delegate;
    return picker;
}

int madeira_folder_picker_bypassed(void)
{
    return madeira_picker_bypassed;
}
