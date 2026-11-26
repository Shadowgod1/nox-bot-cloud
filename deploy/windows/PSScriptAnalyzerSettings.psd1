@{
    Severity     = @('Error', 'Warning')
    ExcludeRules = @(
        # Interactive installer: coloured console output is the point.
        'PSAvoidUsingWriteHost',
        # Script-internal helper functions, not exported cmdlets; -WhatIf is not meaningful here.
        'PSUseShouldProcessForStateChangingFunctions',
        # Script parameters used inside functions via script scope are reported as unused.
        'PSReviewUnusedParameter',
        # Script-internal helpers (Save-PendingKeys, Set-IlerKeys, ConvertTo-Megabytes) read naturally in plural.
        'PSUseSingularNouns'
    )
}
