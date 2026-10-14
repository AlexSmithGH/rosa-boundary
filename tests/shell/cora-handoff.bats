#!/usr/bin/env bats

@test "profile advertises Cora only for interactive shells" {
    profile="${BATS_TEST_DIRNAME}/../../skel/sre/.bash_profile"
    run env HOME="${BATS_TEST_TMPDIR}" bash --noprofile --norc -ic '
        cora-handoff() { printf "handoff called\n"; }
        source "$1"
    ' bash "${profile}"
    [ "${status}" -eq 0 ]
    [[ "${output}" == *"handoff called"* ]]
    run env HOME="${BATS_TEST_TMPDIR}" bash --noprofile --norc -c '
        cora-handoff() { printf "handoff called\n"; }
        source "$1"
    ' bash "${profile}"
    [ "${status}" -eq 0 ]
    [[ "${output}" != *"handoff called"* ]]
}

@test "handoff helper explains its commands" {
    run python3 "${BATS_TEST_DIRNAME}/../../utils/cora-handoff" --help
    [ "${status}" -eq 0 ]
    [[ "${output}" == *"import"* ]]
    [[ "${output}" == *"resume"* ]]
}
