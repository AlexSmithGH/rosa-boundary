# Source global definitions
if [ -f /etc/bashrc ]; then
    . /etc/bashrc
fi

# Source user bashrc
if [ -f ~/.bashrc ]; then
    # The investigation's user-managed bashrc is only available at runtime.
    # shellcheck source=/dev/null
    . ~/.bashrc
fi

# Show the imported investigation on interactive login without invoking a model.
if [[ $- == *i* ]] && command -v cora-handoff >/dev/null 2>&1; then
    cora-handoff show
fi
