"""Fixed, content-free account restrictions detected during Live automation."""
ACCOUNT_BLOCK_ERRORS = {
    'account_checkpoint': 'Facebook requires account verification. Open Facebook and complete its security steps.',
    'account_locked': 'Facebook locked this account. Open Facebook and complete its account recovery steps.',
    'identity_verification_required': 'Facebook requires identity verification before this Page can publish. Open the Facebook app on your phone and follow its instructions.',
}


class LiveAccountBlocked(RuntimeError):
    def __init__(self, code):
        self.account_block = code
        super().__init__(ACCOUNT_BLOCK_ERRORS[code])
